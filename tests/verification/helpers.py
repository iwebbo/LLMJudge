"""Outils partagés par les tests du noyau de vérification (aucun LLM, aucun réseau)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.verification import VerificationContext


def make_chunks(n: int, score: float = 0.8, text: Optional[str] = None) -> List[Dict[str, Any]]:
    return [
        {"text": text if text is not None else f"content of source {i}", "metadata": {"filename": f"file{i}.py"}, "score": score}
        for i in range(1, n + 1)
    ]


def rag_ctx(n_chunks: int = 3, question: str = "How does it work?", score: float = 0.8, **kw: Any) -> VerificationContext:
    return VerificationContext(question=question, retrieved_chunks=make_chunks(n_chunks, score), is_rag=True, **kw)


def chat_ctx(question: str = "Write a function", **kw: Any) -> VerificationContext:
    return VerificationContext(question=question, **kw)


class FakeLint:
    """Faux linter injectable : retourne des violations prédéfinies (ou None = indisponible)."""

    def __init__(self, violations: Optional[List[Dict[str, Any]]] = None):
        self.violations = violations
        self.calls: List[str] = []

    def check(self, code: str):
        self.calls.append(code)
        return None if self.violations is None else list(self.violations)


def violation(code: str, message: str = "msg", row: int = 1) -> Dict[str, Any]:
    return {"code": code, "message": message, "location": {"row": row, "column": 1}}


def codes(result) -> List[str]:
    return [f.code for f in result.findings]
