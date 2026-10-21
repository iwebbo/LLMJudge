from app.services.verification import RetrievalCheck, VerificationContext
from app.services.verification.base import Status

from .helpers import codes, rag_ctx


def test_good_score_passes():
    result = RetrievalCheck(min_score=0.25).run("x", rag_ctx(3, score=0.6), [])
    assert result.status == Status.PASSED
    assert "best=0.60" in result.note


def test_low_best_score_is_warning():
    result = RetrievalCheck(min_score=0.25).run("x", rag_ctx(3, score=0.1), [])
    assert codes(result) == ["retrieval.low_relevance"]
    assert result.findings[0].evidence["threshold"] == 0.25


def test_one_good_chunk_is_enough():
    ctx = VerificationContext(is_rag=True, retrieved_chunks=[
        {"text": "a", "score": 0.05}, {"text": "b", "score": 0.5}, {"text": "c", "score": 0.1}])
    assert RetrievalCheck(0.25).run("x", ctx, []).status == Status.PASSED


def test_no_chunks_is_warning():
    result = RetrievalCheck().run("x", rag_ctx(0), [])
    assert codes(result) == ["retrieval.no_context"]


def test_chunks_without_scores_are_skipped():
    ctx = VerificationContext(is_rag=True, retrieved_chunks=[{"text": "a"}])
    assert RetrievalCheck().run("x", ctx, []).status == Status.SKIPPED


def test_not_rag_is_skipped():
    assert RetrievalCheck().run("x", VerificationContext(), []).status == Status.SKIPPED


def test_threshold_is_configurable():
    assert RetrievalCheck(0.9).run("x", rag_ctx(2, score=0.6), []).status == Status.FAILED
