from app.services.verification.base import (
    CheckResult, Finding, Severity, Status, extract_fenced_blocks, extract_json_object,
    max_severity, strip_fenced_blocks, strip_think,
)


class TestFencedBlocks:
    def test_closed_block_with_lang(self):
        blocks = extract_fenced_blocks("intro\n```python\nprint(1)\n```\noutro")
        assert len(blocks) == 1
        assert blocks[0].lang == "python"
        assert blocks[0].code == "print(1)"
        assert blocks[0].closed is True
        assert blocks[0].start_line == 2

    def test_unclosed_block_is_flagged(self):
        blocks = extract_fenced_blocks("```python\nprint(1)\n")
        assert len(blocks) == 1
        assert blocks[0].closed is False

    def test_tilde_fence_and_info_string(self):
        blocks = extract_fenced_blocks("~~~yaml title=x\na: 1\n~~~")
        assert blocks[0].lang == "yaml"
        assert blocks[0].closed is True

    def test_longer_fence_is_not_closed_by_shorter(self):
        text = "````md\n```python\nx = 1\n```\n````"
        blocks = extract_fenced_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].lang == "md"
        assert "```python" in blocks[0].code
        assert blocks[0].closed is True

    def test_multiple_blocks_and_no_lang(self):
        blocks = extract_fenced_blocks("```\na\n```\ntext\n```js\nb\n```")
        assert [b.lang for b in blocks] == ["", "js"]

    def test_no_block(self):
        assert extract_fenced_blocks("just prose with `inline` code") == []

    def test_strip_fenced_blocks_removes_code_and_keeps_prose(self):
        out = strip_fenced_blocks("before\n```py\nsecret\n```\nafter")
        assert "secret" not in out and "before" in out and "after" in out

    def test_strip_fenced_blocks_unclosed_drops_the_rest(self):
        out = strip_fenced_blocks("before\n```py\nsecret")
        assert "secret" not in out


class TestStripThink:
    def test_complete_block(self):
        assert strip_think("<think>hmm</think>Answer") == "Answer"

    def test_unclosed_think_drops_the_tail(self):
        assert strip_think("Intro <think>still thinking...") == "Intro"

    def test_orphan_closing_tag_keeps_what_follows(self):
        assert strip_think("reasoning here</think>The answer") == "The answer"

    def test_multiline_and_case(self):
        assert strip_think("<THINK>a\nb</THINK>\nok") == "ok"

    def test_none_and_empty(self):
        assert strip_think("") == ""
        assert strip_think(None) == ""


class TestExtractJson:
    def test_plain(self):
        assert extract_json_object('{"a": 1}') == {"a": 1}

    def test_fenced_and_think(self):
        assert extract_json_object('<think>x</think>```json\n{"a": 1}\n```') == {"a": 1}

    def test_surrounded_by_text(self):
        assert extract_json_object('Sure! {"a": {"b": 2}} hope it helps') == {"a": {"b": 2}}

    def test_garbage(self):
        assert extract_json_object("no json here") is None
        assert extract_json_object("{broken") is None


class TestResultTypes:
    def test_severity_ordering(self):
        assert max_severity([Severity.INFO, Severity.ERROR, Severity.WARNING]) == Severity.ERROR
        assert max_severity([]) is None

    def test_from_findings_status(self):
        assert CheckResult.from_findings("x", []).status == Status.PASSED
        failed = CheckResult.from_findings("x", [Finding("c", Severity.WARNING, "m")])
        assert failed.status == Status.FAILED and failed.severity == Severity.WARNING

    def test_finding_evidence_is_clipped(self):
        data = Finding("c", Severity.ERROR, "m", {"text": "x" * 5000}).to_dict(check="chk")
        assert len(data["evidence"]["text"]) <= 300
        assert data["check"] == "chk"
