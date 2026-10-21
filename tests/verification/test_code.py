import pytest

from app.services.verification import CodeBlockCheck, RuffRunner, VerificationContext
from app.services.verification.base import Severity, Status
from app.services.verification.code import ruff_severity

from .helpers import FakeLint, codes, violation

CTX = VerificationContext()
REAL_RUFF = RuffRunner()
needs_ruff = pytest.mark.skipif(not REAL_RUFF.available, reason="ruff is not installed")


# ruff confirme l'erreur de syntaxe (E999) : ast + ruff d'accord => ERROR
SYNTAX_LINT = FakeLint([violation("E999", "SyntaxError")])


def run(response, lint=None, use_ruff=False):
    check = CodeBlockCheck(lint=lint if lint is not None else FakeLint([]), use_ruff=use_ruff)
    return check.run(response, CTX, [])


def py(code):
    return f"```python\n{code}\n```"


class TestGeneral:
    def test_no_code_block_is_skipped(self):
        assert run("plain prose").status == Status.SKIPPED

    def test_declares_blocking_so_pipeline_uses_a_thread(self):
        assert CodeBlockCheck.blocking is True

    def test_unclosed_fence_means_truncation(self):
        result = run("```python\ndef f():\n    return 1")
        assert codes(result) == ["code.unclosed_fence"]
        assert result.findings[0].severity == Severity.ERROR


class TestPython:
    def test_valid_code_passes(self):
        assert run(py("def add(a, b):\n    return a + b")).status == Status.PASSED

    def test_syntax_error_is_error_with_location(self):
        result = run(py("def broken(:\n    pass"), lint=SYNTAX_LINT)
        assert codes(result) == ["code.python_syntax"]
        finding = result.findings[0]
        assert finding.severity == Severity.ERROR
        assert finding.evidence["line"] == 1

    def test_indented_snippet_is_dedented(self):
        snippet = "    def run(self):\n        return 1"
        assert run(py(snippet)).status == Status.PASSED

    def test_py_alias_and_python3(self):
        assert codes(run("```py\ndef x(:\n```", lint=SYNTAX_LINT)) == ["code.python_syntax"]
        assert codes(run("```python3\ndef x(:\n```", lint=SYNTAX_LINT)) == ["code.python_syntax"]

    def test_repl_and_shell_transcripts_are_ignored(self):
        assert run(py(">>> print(1)\n1")).status == Status.PASSED
        assert run(py("$ pip install fastapi")).status == Status.PASSED

    def test_untagged_block_is_not_analysed(self):
        assert run("```\ndef x(:\n```").status == Status.PASSED

    def test_empty_python_block(self):
        assert run("```python\n\n```").status == Status.PASSED

    def test_each_broken_block_is_reported(self):
        text = py("def a(:") + "\ntext\n" + py("def b(:")
        assert codes(run(text, lint=SYNTAX_LINT)) == ["code.python_syntax", "code.python_syntax"]

    def test_think_content_is_not_analysed(self):
        text = "<think>```python\ndef x(:\n```</think>All good, no code."
        assert run(text).status == Status.SKIPPED


class TestLintMapping:
    def test_severity_mapping(self):
        assert ruff_severity("E999") == Severity.ERROR
        assert ruff_severity("E902") == Severity.ERROR
        assert ruff_severity("F706") == Severity.ERROR
        assert ruff_severity("F631") == Severity.ERROR
        assert ruff_severity("F823") == Severity.ERROR
        assert ruff_severity("F821") == Severity.WARNING   # un extrait n'a pas ses imports
        assert ruff_severity("F811") == Severity.WARNING
        assert ruff_severity("B006") == Severity.WARNING
        assert ruff_severity("S307") == Severity.WARNING

    def test_violations_become_findings(self):
        lint = FakeLint([violation("F821", "Undefined name `foo`", 3), violation("F706", "return outside", 5)])
        result = run(py("x = 1"), lint=lint)
        assert codes(result) == ["code.ruff.F821", "code.ruff.F706"]
        assert [f.severity for f in result.findings] == [Severity.WARNING, Severity.ERROR]
        assert "line 3" in result.findings[0].message

    def test_lint_is_not_called_when_syntax_is_invalid_and_no_lint_needed(self):
        lint = FakeLint([violation("E999")])
        result = run(py("def x(:"), lint=lint)
        assert codes(result) == ["code.python_syntax"]

    def test_unavailable_linter_is_silent(self):
        assert run(py("x = 1"), lint=FakeLint(None)).status == Status.PASSED

    def test_newer_syntax_is_info_when_linter_accepts_it(self):
        # ast (runtime 3.11) refuse, ruff n'y voit aucune erreur de syntaxe
        result = run(py("def x(:"), lint=FakeLint([]))
        assert codes(result) == ["code.python_newer_syntax"]
        assert result.findings[0].severity == Severity.INFO

    def test_syntax_error_confirmed_by_linter_stays_error(self):
        result = run(py("def x(:"), lint=FakeLint([violation("E999", "SyntaxError")]))
        assert codes(result) == ["code.python_syntax"]

    def test_violations_are_capped(self):
        lint = FakeLint([violation("F821", f"n{i}", i) for i in range(50)])
        assert len(run(py("x = 1"), lint=lint).findings) == 10


@needs_ruff
class TestRealRuff:
    def real(self, code):
        return CodeBlockCheck(lint=REAL_RUFF).run(py(code), CTX, [])

    def test_clean_code(self):
        assert self.real("def add(a, b):\n    return a + b").status == Status.PASSED

    def test_undefined_name_is_warning(self):
        result = self.real("print(undefined_name)")
        assert codes(result) == ["code.ruff.F821"]
        assert result.findings[0].severity == Severity.WARNING

    def test_eval_and_shell_true_are_flagged(self):
        assert "code.ruff.S307" in codes(self.real("x = eval(input())"))
        assert "code.ruff.S602" in codes(self.real("import subprocess\nsubprocess.run('ls', shell=True)"))

    def test_mutable_default_argument(self):
        assert codes(self.real("def f(a=[]):\n    return a")) == ["code.ruff.B006"]

    def test_return_outside_function_is_error(self):
        result = self.real("return 1")
        assert result.findings[0].severity == Severity.ERROR

    def test_python_312_syntax_does_not_produce_an_error_on_a_311_runtime(self):
        result = self.real("type Alias = int")
        assert all(f.severity != Severity.ERROR for f in result.findings)

    def test_ruff_never_modifies_anything(self):
        # --no-fix : le code d'entrée n'est jamais réécrit, on ne reçoit que des violations
        assert REAL_RUFF.check("import os\n") == []  # F401 n'est pas dans la sélection


class TestJsonYaml:
    def test_valid_json(self):
        assert run('```json\n{"a": [1, 2]}\n```').status == Status.PASSED

    def test_invalid_json_is_warning(self):
        result = run('```json\n{"a": 1,}\n```')
        assert codes(result) == ["code.json_invalid"]
        assert result.findings[0].severity == Severity.WARNING

    def test_illustrative_json_is_only_info(self):
        for sample in ('{\n  "items": [\n    ...\n  ]\n}', '{"a": 1, // comment\n "b": 2}', '{"name": <your-name>}'):
            result = run(f"```json\n{sample}\n```")
            assert [f.severity for f in result.findings] == [Severity.INFO], sample

    def test_valid_yaml(self):
        assert run("```yaml\na: 1\nb:\n  - x\n```").status == Status.PASSED

    def test_invalid_yaml_is_warning(self):
        result = run("```yaml\na: [1, 2\nb: : :\n```")
        assert codes(result) == ["code.yaml_invalid"]

    def test_helm_and_jinja_templates_are_not_flagged(self):
        helm = "```yaml\nimage: \"{{ .Values.backend.image.repository }}:{{ .Values.backend.image.tag }}\"\n{{- if .Values.x }}\nfoo: bar\n{{- end }}\n```"
        assert run(helm).status == Status.PASSED
        assert run("```yaml\n{% if x %}\na: 1\n{% endif %}\n```").status == Status.PASSED

    def test_multi_document_yaml(self):
        assert run("```yaml\na: 1\n---\nb: 2\n```").status == Status.PASSED

    def test_yml_alias(self):
        assert codes(run("```yml\na: [1, 2\n```")) == ["code.yaml_invalid"]


class TestLaziness:
    @pytest.mark.parametrize("snippet", [
        "def f():\n    # ... rest of the code\n    pass",
        "def f():\n    # your code here\n    pass",
        "function f() {\n  // TODO: implement\n}",
        "def f():\n    pass  # implement later",
        "# ... le reste inchangé\nx = 1",
        "// ... existing code\nconst a = 1;",
    ])
    def test_placeholders_are_warned(self, snippet):
        lang = "javascript" if "function" in snippet or "const" in snippet else "python"
        result = run(f"```{lang}\n{snippet}\n```", lint=FakeLint([]))
        assert "code.placeholder" in codes(result)
        assert all(f.severity == Severity.WARNING for f in result.findings if f.code == "code.placeholder")

    def test_normal_comments_are_fine(self):
        assert run(py("# compute the total\ntotal = 1 + 2")).status == Status.PASSED

    def test_not_applied_to_prose_blocks(self):
        assert run("```text\n# your code here\n```").status == Status.PASSED

    def test_one_finding_per_block(self):
        snippet = "# ... rest of the code\n# your code here\n# TODO: implement"
        assert codes(run(f"```python\n{snippet}\n```")).count("code.placeholder") == 1


class TestRuffVersionCompatibility:
    """Les erreurs de syntaxe sont nommées E999 (ruff <= 0.3), 'invalid-syntax' ou code nul (versions récentes)."""

    @pytest.mark.parametrize("violation", [
        {"code": "E999", "message": "SyntaxError: Unexpected token", "location": {"row": 1}},
        {"code": "invalid-syntax", "name": "invalid-syntax", "message": "Expected `)`", "location": {"row": 1}},
        {"code": None, "message": "SyntaxError: Unexpected token ':'", "location": {"row": 1}},
    ])
    def test_syntax_error_stays_an_error_whatever_the_ruff_version(self, violation):
        result = run(py("def broken(:\n    pass"), lint=FakeLint([violation]))
        assert codes(result) == ["code.python_syntax"]
        assert result.findings[0].severity == Severity.ERROR

    def test_ruff_severity_knows_new_syntax_codes(self):
        assert ruff_severity("invalid-syntax") == Severity.ERROR
        assert ruff_severity("E999") == Severity.ERROR

    @needs_ruff
    def test_installed_ruff_flags_a_real_syntax_error_as_an_error(self):
        result = CodeBlockCheck(lint=REAL_RUFF).run(py("def broken(:\n    pass"), CTX, [])
        assert codes(result) == ["code.python_syntax"]
