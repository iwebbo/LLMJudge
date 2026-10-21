from app.services.verification import SchemaCheck, VerificationContext, validate_schema
from app.services.verification.base import Status

from .helpers import codes

SCHEMA = {
    "type": "object",
    "required": ["summary", "features"],
    "properties": {
        "summary": {"type": "string", "minLength": 3},
        "features": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["title"]}},
        "level": {"type": "string", "enum": ["low", "high"]},
    },
}


def run(response, **ctx):
    return SchemaCheck().run(response, VerificationContext(**ctx), [])


def test_skipped_without_expectation():
    assert run("anything").status == Status.SKIPPED


def test_json_expected_and_valid():
    assert run('{"a": 1}', expected_format="json").status == Status.PASSED


def test_json_expected_but_prose():
    assert codes(run("Here you go!", expected_format="json")) == ["schema.invalid_json"]


def test_json_in_fence_with_think():
    text = '<think>hm</think>\n```json\n{"summary": "abc", "features": [{"title": "x"}]}\n```'
    assert run(text, expected_schema=SCHEMA).status == Status.PASSED


def test_missing_required_key():
    result = run('{"summary": "abc"}', expected_schema=SCHEMA)
    assert codes(result) == ["schema.violation"]
    assert "features" in result.findings[0].message


def test_wrong_types_and_nested_paths():
    result = run('{"summary": 5, "features": [{"nope": 1}]}', expected_schema=SCHEMA)
    messages = " | ".join(f.message for f in result.findings)
    assert "$.summary" in messages and "$.features[0]" in messages


def test_enum_and_lengths_and_items():
    bad = '{"summary": "ab", "features": [], "level": "mid"}'
    messages = " | ".join(f.message for f in run(bad, expected_schema=SCHEMA).findings)
    assert "shorter than 3" in messages and "fewer than 1" in messages and "not in" in messages


def test_yaml_expected():
    assert run("a: 1\nb: [x]", expected_format="yaml").status == Status.PASSED
    assert codes(run("a: [1, 2\n", expected_format="yaml")) == ["schema.invalid_yaml"]


def test_yaml_in_fence_with_schema():
    text = "```yaml\nsummary: abc\nfeatures:\n  - title: x\n```"
    assert run(text, expected_format="yaml", expected_schema=SCHEMA).status == Status.PASSED


def test_unsupported_format_is_skipped():
    assert run("x", expected_format="xml").status == Status.SKIPPED


def test_validate_schema_types():
    assert validate_schema(True, {"type": "integer"})            # bool n'est pas un entier
    assert not validate_schema(3, {"type": ["integer", "null"]})
    assert not validate_schema(None, {"type": ["integer", "null"]})
    assert validate_schema("x", {"type": "number"})
