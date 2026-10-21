import json
from types import SimpleNamespace

from app.services.verification import (
    JudgeCheck, RetrievalCheck, build_pipeline, format_sse, pipeline_from_settings, resolve_mode,
    status_payload, verdict_payload, SSE_KEEPALIVE,
)
from app.services.verification.pipeline import Verdict


async def _complete(messages):
    return "{}"


def names(p):
    return [type(c).__name__ for c in p.checks]


def test_rag_pipeline_order_judge_last():
    assert names(build_pipeline(is_rag=True, judge_complete=_complete)) == [
        "SanityCheck", "CitationCheck", "RetrievalCheck", "CodeBlockCheck", "SchemaCheck", "JudgeCheck"]


def test_chat_pipeline_has_no_rag_checks():
    assert names(build_pipeline(is_rag=False, judge_complete=_complete)) == [
        "SanityCheck", "CodeBlockCheck", "SchemaCheck", "JudgeCheck"]


def test_no_judge_when_not_provided_or_off():
    assert "JudgeCheck" not in names(build_pipeline(is_rag=True))
    assert "JudgeCheck" not in names(build_pipeline(is_rag=True, judge_complete=_complete, judge_mode="off"))


def test_settings_are_read_with_defaults():
    p = pipeline_from_settings(SimpleNamespace(), is_rag=True, judge_complete=_complete)
    retrieval = next(c for c in p.checks if isinstance(c, RetrievalCheck))
    judge = next(c for c in p.checks if isinstance(c, JudgeCheck))
    assert retrieval.min_score == 0.25 and judge.mode == "auto" and p.timeout_seconds == 45


def test_settings_override_and_invalid_judge_mode():
    s = SimpleNamespace(VERIFY_JUDGE="always", RAG_MIN_SCORE=0.4, VERIFY_TIMEOUT_SECONDS=10, VERIFY_RUFF=False)
    p = pipeline_from_settings(s, is_rag=True, judge_complete=_complete)
    assert next(c for c in p.checks if isinstance(c, JudgeCheck)).mode == "always"
    assert next(c for c in p.checks if isinstance(c, RetrievalCheck)).min_score == 0.4 and p.timeout_seconds == 10
    p = pipeline_from_settings(SimpleNamespace(VERIFY_JUDGE="bogus"), is_rag=False, judge_complete=_complete)
    assert next(c for c in p.checks if isinstance(c, JudgeCheck)).mode == "auto"


def test_resolve_mode_priority():
    assert resolve_mode(None, SimpleNamespace()) == "annotate"
    assert resolve_mode(None, SimpleNamespace(VERIFY_MODE="gate")) == "gate"
    assert resolve_mode("off", SimpleNamespace(VERIFY_MODE="gate")) == "off"
    assert resolve_mode("junk", SimpleNamespace(VERIFY_MODE="gate")) == "gate"
    assert resolve_mode(None, SimpleNamespace(VERIFY_MODE="junk")) == "annotate"


def test_sse_helpers():
    assert format_sse("x", {"a": 1}) == 'event: x\ndata: {"a": 1}\n\n'
    assert SSE_KEEPALIVE.startswith(":") and SSE_KEEPALIVE.endswith("\n\n")
    assert status_payload({"phase": "verifying", "round": 0}) == {"type": "status", "phase": "verifying", "round": 0}
    payload = verdict_payload(Verdict.unverified("n"))
    assert payload["type"] == "verdict" and json.dumps(payload)
