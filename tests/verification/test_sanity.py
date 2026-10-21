from app.services.verification import SanityCheck, VerificationContext
from app.services.verification.base import Severity, Status

from .helpers import codes

CTX = VerificationContext()


def run(text, **kw):
    return SanityCheck(**kw).run(text, CTX, [])


def test_normal_answer_passes():
    assert run("A perfectly fine answer.").status == Status.PASSED


def test_think_plus_answer_passes():
    assert run("<think>let me see</think>Done.").status == Status.PASSED


def test_empty_and_whitespace():
    assert codes(run("")) == ["sanity.empty"]
    assert codes(run("   \n ")) == ["sanity.empty"]


def test_reasoning_only():
    result = run("<think>long reasoning</think>")
    assert codes(result) == ["sanity.empty"]
    assert "only reasoning" in result.findings[0].message


def test_unclosed_reasoning_means_truncated_generation():
    result = run("<think>I should consider many things and")
    assert codes(result) == ["sanity.reasoning_truncated"]
    assert result.findings[0].severity == Severity.ERROR


def test_repetition_loop_detected():
    text = "intro\n" + "\n".join(["I will now write the code."] * 7)
    result = run(text)
    assert codes(result) == ["sanity.repetition_loop"]
    assert result.findings[0].evidence["count"] >= 6


def test_few_repetitions_are_fine():
    assert run("\n".join(["Same sentence repeated."] * 5)).status == Status.PASSED


def test_short_repeated_lines_are_ignored():
    assert run("\n".join(["-"] * 20 + ["ok"] * 20)).status == Status.PASSED


def test_repetition_inside_think_is_ignored():
    text = "<think>" + "\n".join(["thinking about it again"] * 20) + "</think>Answer here."
    assert run(text).status == Status.PASSED
