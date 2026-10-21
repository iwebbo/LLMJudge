from app.services.verification import CitationCheck, VerificationContext, extract_citation_numbers
from app.services.verification.base import Severity, Status

from .helpers import codes, make_chunks, rag_ctx


def run(response, ctx=None, **kw):
    return CitationCheck(**kw).run(response, ctx or rag_ctx(3), [])


class TestExtraction:
    def test_simple_and_multiple(self):
        assert extract_citation_numbers("a [SOURCE 1] b [SOURCE 3] c [SOURCE 1]") == [1, 3]

    def test_lists_ranges_and_variants(self):
        assert extract_citation_numbers("[SOURCE 1, SOURCE 2]") == [1, 2]
        assert extract_citation_numbers("[SOURCE 1-3]") == [1, 2, 3]
        assert extract_citation_numbers("[Source: 2]") == [2]
        assert extract_citation_numbers("[SOURCES 1 et 2]") == [1, 2]
        assert extract_citation_numbers("[source 4 and 6]") == [4, 6]

    def test_huge_range_is_not_expanded(self):
        assert extract_citation_numbers("[SOURCE 1-999]") == [1, 999]

    def test_ignored_inside_code_and_think(self):
        text = "<think>[SOURCE 8]</think>ok\n```python\nx = '[SOURCE 9]'\n```"
        assert extract_citation_numbers(text) == []

    def test_unrelated_brackets(self):
        assert extract_citation_numbers("see [1] and [link](http://x) and [SOURCE]") == []


class TestCheck:
    def test_valid_citations_pass(self):
        result = run("Foo [SOURCE 1] bar [SOURCE 3].")
        assert result.status == Status.PASSED

    def test_out_of_range_is_error(self):
        result = run("Foo [SOURCE 4].")
        assert codes(result) == ["citation.out_of_range"]
        assert result.findings[0].severity == Severity.ERROR
        assert result.findings[0].evidence == {"cited": 4, "available": 3}

    def test_zero_is_out_of_range(self):
        assert codes(run("[SOURCE 0]")) == ["citation.out_of_range"]

    def test_range_partially_out_of_range(self):
        result = run("[SOURCE 2-5]")
        assert sorted(f.evidence["cited"] for f in result.findings) == [4, 5]

    def test_citation_without_any_retrieved_chunk(self):
        result = run("According to [SOURCE 1]...", rag_ctx(0))
        assert codes(result) == ["citation.out_of_range"]
        assert "no source was retrieved" in result.findings[0].message

    def test_missing_citations_on_long_rag_answer_is_warning(self):
        result = run("word " * 80)
        assert codes(result) == ["citation.missing"]
        assert result.findings[0].severity == Severity.WARNING

    def test_short_answer_without_citation_is_fine(self):
        assert run("Yes.").status == Status.PASSED

    def test_missing_citations_not_required(self):
        assert run("word " * 80, require_citations=False).status == Status.PASSED

    def test_no_chunks_no_citations_is_fine(self):
        assert run("word " * 80, rag_ctx(0)).status == Status.PASSED

    def test_empty_cited_source_is_warning(self):
        ctx = VerificationContext(is_rag=True, retrieved_chunks=make_chunks(2, text="   "))
        result = run("see [SOURCE 2]", ctx)
        assert codes(result) == ["citation.empty_source"]

    def test_not_rag_and_no_citation_is_skipped(self):
        result = run("hello", VerificationContext(is_rag=False))
        assert result.status == Status.SKIPPED

    def test_not_rag_but_cites_is_error(self):
        # le modèle invente des citations alors qu'il n'a reçu aucun document
        result = run("see [SOURCE 1]", VerificationContext(is_rag=False))
        assert codes(result) == ["citation.out_of_range"]
