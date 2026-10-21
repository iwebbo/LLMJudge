import asyncio
import time

from app.services.verification import VerificationContext, VerificationPipeline
from app.services.verification.base import CheckResult, Finding, Severity, Status
from app.services.verification.pipeline import Verdict


class Static:
    def __init__(self, name, *findings, status=None, blocking=False):
        self.name, self._findings, self.blocking = name, list(findings), blocking
        self._status = status

    def run(self, response, ctx, prior):
        if self._status == Status.SKIPPED:
            return CheckResult.skipped(self.name)
        return CheckResult.from_findings(self.name, self._findings)


class Boom:
    name = "boom"

    def run(self, response, ctx, prior):
        raise RuntimeError("kaput")


class Slow:
    name = "slow"

    async def run(self, response, ctx, prior):
        await asyncio.sleep(5)
        return CheckResult.from_findings(self.name, [])


class Spy:
    name = "spy"

    def __init__(self):
        self.prior = None

    def run(self, response, ctx, prior):
        self.prior = [r.name for r in prior]
        return CheckResult.from_findings(self.name, [])


def f(sev, code="x.y"):
    return Finding(code, sev, f"{sev.value} message")


def run(checks, **kw):
    return asyncio.run(VerificationPipeline(checks, **kw).run("resp", VerificationContext()))


def test_all_clear_is_verified_with_score_one():
    v = run([Static("a"), Static("b")])
    assert (v.passed, v.score, v.status) == (True, 1.0, "verified")


def test_penalties_and_status():
    v = run([Static("a", f(Severity.WARNING), f(Severity.WARNING))])
    assert v.status == "warnings" and v.passed and abs(v.score - 0.8) < 1e-9
    v = run([Static("a", f(Severity.ERROR), f(Severity.WARNING))])
    assert v.status == "failed" and not v.passed and abs(v.score - 0.55) < 1e-9


def test_info_has_no_penalty_and_stays_verified():
    v = run([Static("a", f(Severity.INFO))])
    assert v.score == 1.0 and v.status == "verified"


def test_score_floors_at_zero():
    v = run([Static("a", *[f(Severity.ERROR)] * 5)])
    assert v.score == 0.0


def test_everything_skipped_is_unverified():
    v = run([Static("a", status=Status.SKIPPED)])
    assert v.status == "unverified" and v.passed


def test_crashing_check_is_isolated():
    v = run([Boom(), Static("b", f(Severity.WARNING))])
    assert [r.status for r in v.results] == [Status.ERRORED, Status.FAILED]
    assert v.passed and "kaput" in v.results[0].note


def test_checks_see_prior_results_in_order():
    spy = Spy()
    run([Static("a"), Static("b"), spy])
    assert spy.prior == ["a", "b"]


def test_timeout_returns_partial_verdict():
    started = time.monotonic()
    v = run([Static("a", f(Severity.WARNING)), Slow()], timeout_seconds=0.1)
    assert time.monotonic() - started < 2
    assert v.timed_out and v.passed
    assert [r.name for r in v.results] == ["a", "pipeline"]


def test_blocking_check_runs_in_thread():
    import threading
    seen = {}

    class Threaded:
        name, blocking = "t", True

        def run(self, response, ctx, prior):
            seen["thread"] = threading.current_thread()
            return CheckResult.from_findings("t", [])

    run([Threaded()])
    assert seen["thread"] is not threading.main_thread()


def test_to_dict_is_json_serialisable_and_complete():
    import json
    v = run([Static("a", f(Severity.ERROR, "c.one"))])
    d = json.loads(json.dumps(v.to_dict()))
    assert d["status"] == "failed" and d["issues"][0]["code"] == "c.one" and d["issues"][0]["check"] == "a"
    assert d["checks"][0]["name"] == "a" and "repair_hint" in d and d["rounds"] == 0


def test_repair_hint_orders_errors_first_and_skips_info():
    v = run([Static("a", f(Severity.WARNING), f(Severity.INFO), f(Severity.ERROR))])
    lines = v.repair_hint.splitlines()
    assert lines[0].startswith("- [error]") and lines[1].startswith("- [warning]") and len(lines) == 2


def test_repair_hint_is_capped():
    v = run([Static("a", *[f(Severity.WARNING)] * 30)])
    assert len(v.repair_hint.splitlines()) == 8


def test_needs_repair():
    assert Verdict(passed=False, score=0.9, status="failed").needs_repair(0.7)
    assert Verdict(passed=True, score=0.6, status="warnings").needs_repair(0.7)
    assert not Verdict(passed=True, score=0.9, status="verified").needs_repair(0.7)


def test_unverified_factory():
    v = Verdict.unverified("why")
    assert v.passed and v.status == "unverified" and v.results[0].note == "why"


def test_pipeline_logs_one_info_line_with_checks_and_issues(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="app.services.verification.pipeline"):
        run([Static("a"), Static("b", f(Severity.ERROR, "c.one"))])
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("verification:")]
    assert len(lines) == 1
    assert "status=failed" in lines[0] and "a:passed(" in lines[0] and "b:failed(" in lines[0]
    assert "c.one[error]" in lines[0]
