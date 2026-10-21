import asyncio

import pytest

from app.services.verification import (
    VerificationContext,
    VerificationPipeline,
    normalize_mode,
    run_verified_stream,
)
from app.services.verification.base import CheckResult, Finding, Severity
from app.services.verification.orchestrator import build_repair_messages
from app.services.verification.pipeline import Verdict

CTX = VerificationContext()
MSGS = [{"role": "user", "content": "question"}]


class Scripted:
    """Check dont le verdict dépend du texte : contient 'BAD' => erreur."""
    name = "scripted"

    def __init__(self, delay=0.0):
        self.delay, self.seen = delay, []

    async def run(self, response, ctx, prior):
        self.seen.append(response)
        if self.delay:
            await asyncio.sleep(self.delay)
        bad = "BAD" in response
        return CheckResult.from_findings(self.name, [Finding("t.bad", Severity.ERROR, "contains BAD")] if bad else [])


class Opener:
    def __init__(self, *answers, fail_at=None):
        self.answers, self.calls, self.fail_at = list(answers), [], fail_at

    def __call__(self, messages):
        self.calls.append(messages)
        index = len(self.calls) - 1
        answer = self.answers[min(index, len(self.answers) - 1)]

        async def gen():
            if self.fail_at == index:
                raise RuntimeError("provider down")
            for i in range(0, len(answer), 5):
                yield answer[i:i + 5]
        return gen()


def collect(**kw):
    async def go():
        return [e async for e in run_verified_stream(**kw)]
    return asyncio.run(go())


def kinds(events):
    return [e.kind for e in events]


def text(events):
    return "".join(e.data for e in events if e.kind == "token")


def pipe(check=None, **kw):
    return VerificationPipeline([check or Scripted()], **kw)


def test_normalize_mode():
    assert normalize_mode("GATE") == "gate" and normalize_mode(" off ") == "off"
    assert normalize_mode("nope") == "annotate" and normalize_mode(None, "gate") == "gate"
    assert normalize_mode("nope", "weird") == "annotate"


def test_off_is_pure_passthrough_and_never_verifies():
    check = Scripted()
    ev = collect(open_stream=Opener("hello BAD world"), messages=MSGS, ctx=CTX, pipeline=pipe(check), mode="off")
    assert kinds(ev)[-1] == "final" and "verdict" not in kinds(ev) and not check.seen
    assert text(ev) == "hello BAD world"


def test_no_pipeline_behaves_like_off():
    ev = collect(open_stream=Opener("abc"), messages=MSGS, ctx=CTX, pipeline=None, mode="gate")
    assert "verdict" not in kinds(ev) and text(ev) == "abc"


def test_annotate_streams_then_final_then_verdict():
    ev = collect(open_stream=Opener("hello BAD world"), messages=MSGS, ctx=CTX, pipeline=pipe(), mode="annotate")
    k = kinds(ev)
    assert k.index("final") < k.index("verdict") and k[-1] == "verdict"
    assert k.index("token") < k.index("final")
    assert text(ev) == "hello BAD world"
    verdict = ev[-1].data
    assert not verdict.passed and verdict.rounds == 0  # annotate ne régénère jamais


def test_annotate_final_before_verification_starts():
    check = Scripted()
    ev = collect(open_stream=Opener("ok"), messages=MSGS, ctx=CTX, pipeline=pipe(check), mode="annotate")
    assert kinds(ev)[:3][-1] in ("final", "status") and "final" in kinds(ev)[:3]


def test_annotate_keepalive_while_verifying():
    ev = collect(open_stream=Opener("ok"), messages=MSGS, ctx=CTX, pipeline=pipe(Scripted(delay=0.35)),
                 mode="annotate", keepalive_seconds=0.1)
    assert kinds(ev).count("keepalive") >= 2 and kinds(ev)[-1] == "verdict"


def test_gate_passes_first_time_without_repair():
    opener = Opener("all good")
    ev = collect(open_stream=opener, messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate")
    assert len(opener.calls) == 1 and text(ev) == "all good"
    verdict = ev[-1].data
    assert verdict.passed and verdict.rounds == 0 and not verdict.repaired
    assert kinds(ev)[-1] == "verdict" and kinds(ev).index("final") < kinds(ev).index("token")


def test_gate_buffers_nothing_before_verification():
    ev = collect(open_stream=Opener("all good"), messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate")
    k = kinds(ev)
    assert k.index("token") > max(i for i, x in enumerate(k) if x == "status" and ev[i].data["phase"] == "verifying")


def test_gate_repairs_with_findings_and_delivers_fixed_version():
    opener = Opener("draft BAD", "fixed answer")
    ev = collect(open_stream=opener, messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate")
    assert text(ev) == "fixed answer"
    assert len(opener.calls) == 2
    repair = opener.calls[1]
    assert repair[:1] == MSGS and repair[1] == {"role": "assistant", "content": "draft BAD"}
    assert "contains BAD" in repair[2]["content"]
    verdict = ev[-1].data
    assert verdict.passed and verdict.repaired and verdict.rounds == 1
    phases = [e.data["phase"] for e in ev if e.kind == "status"]
    assert phases == ["generating", "verifying", "repairing", "verifying"]
    assert [e.data for e in ev if e.kind == "final"] == ["fixed answer"]


def test_gate_gives_up_after_max_rounds_and_delivers_best_with_failed_verdict():
    opener = Opener("BAD 1")
    ev = collect(open_stream=opener, messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate", max_rounds=2)
    assert len(opener.calls) == 3
    verdict = ev[-1].data
    assert not verdict.passed and verdict.rounds == 2
    assert text(ev) == "BAD 1"  # jamais retenue silencieusement


def test_gate_keeps_best_candidate_not_last():
    class Graded(Scripted):
        async def run(self, response, ctx, prior):
            n = response.count("BAD")
            return CheckResult.from_findings(self.name, [Finding("t.bad", Severity.ERROR, "bad")] * n)

    opener = Opener("BAD", "BAD BAD BAD", "BAD BAD BAD")
    ev = collect(open_stream=opener, messages=MSGS, ctx=CTX, pipeline=pipe(Graded()), mode="gate", max_rounds=2)
    assert text(ev) == "BAD"


def test_gate_max_rounds_zero_never_repairs():
    opener = Opener("BAD")
    ev = collect(open_stream=opener, messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate", max_rounds=0)
    assert len(opener.calls) == 1 and ev[-1].data.rounds == 0


def test_gate_replays_in_chunks():
    long = "x" * 1000
    ev = collect(open_stream=Opener(long), messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate", replay_chunk_chars=120)
    tokens = [e.data for e in ev if e.kind == "token"]
    assert len(tokens) == 9 and "".join(tokens) == long


def test_gate_keepalive_during_slow_generation():
    class Slow(Opener):
        def __call__(self, messages):
            async def gen():
                await asyncio.sleep(0.35)
                yield "late"
            return gen()

    ev = collect(open_stream=Slow("x"), messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate", keepalive_seconds=0.1)
    assert kinds(ev).count("keepalive") >= 2 and text(ev) == "late"


def test_generation_error_propagates():
    with pytest.raises(RuntimeError, match="provider down"):
        collect(open_stream=Opener("x", fail_at=0), messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate")
    with pytest.raises(RuntimeError, match="provider down"):
        collect(open_stream=Opener("x", fail_at=0), messages=MSGS, ctx=CTX, pipeline=pipe(), mode="annotate")


def test_repair_generation_error_propagates():
    with pytest.raises(RuntimeError):
        collect(open_stream=Opener("BAD", fail_at=1), messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate")


def test_pipeline_crash_yields_unverified_not_exception():
    class Exploding:
        async def run(self, *a, **k):
            raise ValueError("pipeline bug")

    ev = collect(open_stream=Opener("hello"), messages=MSGS, ctx=CTX, pipeline=Exploding(), mode="annotate")
    assert ev[-1].kind == "verdict" and ev[-1].data.status == "unverified" and ev[-1].data.passed
    ev = collect(open_stream=Opener("hello"), messages=MSGS, ctx=CTX, pipeline=Exploding(), mode="gate")
    assert text(ev) == "hello" and ev[-1].data.status == "unverified"


def test_client_disconnect_cancels_verification():
    started, cancelled = asyncio.Event(), []

    class Hang:
        name = "hang"

        async def run(self, *a):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

    async def go():
        gen = run_verified_stream(open_stream=Opener("ok"), messages=MSGS, ctx=CTX,
                                  pipeline=VerificationPipeline([Hang()]), mode="annotate", keepalive_seconds=0.05)
        async for event in gen:
            if event.kind == "keepalive":
                break
        await gen.aclose()
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert cancelled == [True]


def test_build_repair_messages_does_not_mutate_input():
    v = Verdict(passed=False, score=0.5, status="failed",
                results=[CheckResult.from_findings("c", [Finding("a.b", Severity.ERROR, "oops")])])
    original = list(MSGS)
    out = build_repair_messages(MSGS, "draft", v)
    assert MSGS == original and len(out) == 3 and "oops" in out[2]["content"]


def test_gate_logs_each_round_and_final_decision(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="app.services.verification"):
        collect(open_stream=Opener("draft BAD", "fixed"), messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate")
    text_log = caplog.text
    assert "verification start: mode=gate" in text_log
    assert "gate round 0/2" in text_log and "needs_repair=True (error)" in text_log
    assert "gate round 1/2" in text_log and "needs_repair=False (ok)" in text_log
    assert "gate delivered: status=verified" in text_log and "repaired=True" in text_log


def test_gate_logs_a_warning_when_rounds_are_exhausted(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="app.services.verification"):
        collect(open_stream=Opener("BAD"), messages=MSGS, ctx=CTX, pipeline=pipe(), mode="gate", max_rounds=1)
    assert any("repair rounds exhausted" in r.message and r.levelno == logging.WARNING for r in caplog.records)
