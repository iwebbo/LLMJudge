import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

from app.config import load_dotenv

# argument CLI → variable d'environnement AgentRAG correspondante
_VERIFY_ENV = {
    "mode": "VERIFY_MODE", "judge": "VERIFY_JUDGE", "max_rounds": "VERIFY_MAX_ROUNDS",
    "min_score": "VERIFY_MIN_SCORE", "rag_min_score": "RAG_MIN_SCORE", "timeout": "VERIFY_TIMEOUT_SECONDS",
    "verifier_model": "VERIFIER_MODEL", "verifier_provider": "VERIFIER_PROVIDER_NAME",
}


@dataclass
class LabConfig:
    provider: str
    base_url: Optional[str]
    api_key: Optional[str]
    model: str
    temperature: float
    system_prompt: Optional[str]
    reasoning: str
    verifier_base_url: Optional[str]
    verifier_api_key: Optional[str]


def _err(*parts):
    print(*parts, file=sys.stderr, flush=True)


def _common(p):
    g = p.add_argument_group("générateur (env LAB_*)")
    g.add_argument("--provider", help="clé de ProviderFactory : lmstudio, ollama, vllm, localai, openai…")
    g.add_argument("--base-url", help="ex. http://localhost:1234/v1 (LM Studio)")
    g.add_argument("--api-key")
    g.add_argument("--model", help="identifiant exact du modèle chargé")
    g.add_argument("--temperature", type=float, help="portée par la conversation (comme l'UI)")
    g.add_argument("--system-file", help="fichier : prompt système de la conversation")
    g.add_argument("--reasoning", choices=["standard", "cot", "deep"], help="reasoning_mode de la conversation")
    k = p.add_argument_group("vérification (noms et sémantique AgentRAG)")
    k.add_argument("--mode", choices=["off", "annotate", "gate"], help="verify_mode de la requête")
    k.add_argument("--judge", choices=["off", "auto", "always"], help="VERIFY_JUDGE")
    k.add_argument("--max-rounds", type=int, help="VERIFY_MAX_ROUNDS")
    k.add_argument("--min-score", type=float, help="VERIFY_MIN_SCORE")
    k.add_argument("--rag-min-score", type=float, help="RAG_MIN_SCORE")
    k.add_argument("--timeout", type=int, help="VERIFY_TIMEOUT_SECONDS")
    k.add_argument("--verifier-model", help="VERIFIER_MODEL : modèle du juge (même provider)")
    k.add_argument("--verifier-provider", help="VERIFIER_PROVIDER_NAME : autre type de provider pour le juge")
    p.add_argument("-v", "--verbose", action="count", default=0, help="-v tous les logs INFO, -vv DEBUG")


def _prepare(args) -> LabConfig:
    """Fixe l'environnement (avant tout import app.*) et résout la config du générateur."""
    load_dotenv()
    for arg, env in _VERIFY_ENV.items():
        value = getattr(args, arg, None)
        if value is not None:
            os.environ[env] = str(value)

    def pick(arg, env, default=None):
        value = getattr(args, arg, None)
        return value if value is not None else (os.environ.get(env) or default)

    system_prompt = os.environ.get("LAB_SYSTEM_PROMPT") or None
    if getattr(args, "system_file", None):
        with open(args.system_file, encoding="utf-8") as fh:
            system_prompt = fh.read()

    level = logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose == 1 else logging.WARNING
    logging.basicConfig(level=level, stream=sys.stderr, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    # ce qu'on étudie : le processus de vérification (check, re-check, seuils) est toujours visible
    logging.getLogger("app.services.verification").setLevel(logging.DEBUG if args.verbose >= 2 else logging.INFO)
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "sqlalchemy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return LabConfig(
        provider=(pick("provider", "LAB_PROVIDER", "lmstudio")).lower(),
        base_url=pick("base_url", "LAB_BASE_URL"),
        api_key=pick("api_key", "LAB_API_KEY"),
        model=pick("model", "LAB_MODEL", ""),
        temperature=float(pick("temperature", "LAB_TEMPERATURE", 0.7)),
        system_prompt=system_prompt,
        reasoning=pick("reasoning", "LAB_REASONING", "standard"),
        verifier_base_url=os.environ.get("LAB_VERIFIER_BASE_URL"),
        verifier_api_key=os.environ.get("LAB_VERIFIER_API_KEY"),
    )


# ──────────────────────────────────────── ping ────────────────────────────────────────

_BAD_ANSWER = "```python\nimport requests\nresponse = requests.get_json_fast('http://example.com')\nprint(response)\n```"
_GOOD_ANSWER = "```python\ndef add(a, b):\n    return a + b\n```"


async def cmd_ping(args) -> int:
    cfg = _prepare(args)
    from app.config import get_settings
    from app.database import SessionLocal, init_db
    from app.services.provider_factory import ProviderFactory
    from app.services.verification import JudgeCheck, RuffRunner, VerificationContext, make_complete
    from app.services.verification.base import strip_think

    settings = get_settings()
    failures = 0

    def line(ok, label, detail=""):
        nonlocal failures
        failures += 0 if ok else 1
        print(f"  [{'OK' if ok else 'FAIL'}] {label:<22} {detail}")

    print(f"provider={cfg.provider}  base_url={cfg.base_url or '(défaut du provider)'}  model={cfg.model or '(non défini)'}"
          f"  temperature={cfg.temperature}")
    print(f"verify : mode={settings.VERIFY_MODE} judge={settings.VERIFY_JUDGE} max_rounds={settings.VERIFY_MAX_ROUNDS} "
          f"min_score={settings.VERIFY_MIN_SCORE} rag_min_score={settings.RAG_MIN_SCORE} "
          f"timeout={settings.VERIFY_TIMEOUT_SECONDS}s verifier={settings.VERIFIER_PROVIDER_NAME or '(même provider)'}/"
          f"{settings.VERIFIER_MODEL or '(même modèle)'}\n")

    line(bool(cfg.model), "modèle configuré", cfg.model or "définir LAB_MODEL ou --model")

    try:
        init_db()
        db = SessionLocal()
        db.close()
        line(True, "base", settings.DATABASE_URL)
    except Exception as exc:
        line(False, "base", str(exc))

    line(RuffRunner().available, "ruff", "lint Python du CodeBlockCheck" if RuffRunner().available
         else "absent : pip install ruff==0.3.4 (le check code retombe sur ast seul)")

    try:
        provider = ProviderFactory.create_provider(cfg.provider, api_key=cfg.api_key, base_url=cfg.base_url, config={})
        line(True, "factory", type(provider).__name__)
    except Exception as exc:
        line(False, "factory", str(exc))
        return 1

    models = []
    try:
        models = await provider.get_available_models()
    except Exception as exc:
        _err(f"  get_available_models: {exc}")
    if models:
        present = cfg.model in models
        line(present or not cfg.model, "modèles listés", ", ".join(models[:8]) + (" …" if len(models) > 8 else "")
             + ("" if present else f"   ← « {cfg.model} » absent de la liste"))
    else:
        line(False, "modèles listés", "aucun (serveur arrêté ou fallback du provider)")

    started = time.monotonic()
    ttft = None
    text = ""
    try:
        async def generate():
            nonlocal ttft, text
            async for chunk in provider.stream_completion(
                messages=[{"role": "user", "content": "Reply with exactly one word: pong"}],
                model=cfg.model, temperature=0, max_tokens=256,
            ):
                if ttft is None:
                    ttft = int((time.monotonic() - started) * 1000)
                text += chunk

        await asyncio.wait_for(generate(), timeout=settings.VERIFY_TIMEOUT_SECONDS)
        visible = strip_think(text)
        line(bool(visible), "génération (stream)",
             f"ttft={ttft}ms total={int((time.monotonic() - started) * 1000)}ms réponse={visible[:40]!r}"
             + ("  [raisonnement <think> détecté]" if "<think>" in text.lower() else ""))
    except Exception as exc:
        line(False, "génération (stream)", f"{type(exc).__name__}: {exc}")
        print("\n→ le provider ne répond pas : vérifier le serveur, --base-url (avec /v1), --model.")
        return 1

    if settings.VERIFY_JUDGE != "off":
        judge_provider, judge_model = provider, settings.VERIFIER_MODEL or cfg.model
        complete = make_complete(judge_provider, judge_model)
        judge = JudgeCheck(complete, mode="always")
        ctx = VerificationContext(question="Show how to fetch a URL in Python.")
        for label, answer, expect_issue in (("juge · API inventée", _BAD_ANSWER, True),
                                            ("juge · code correct", _GOOD_ANSWER, False)):
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(judge.run(answer, ctx, []), timeout=settings.VERIFY_TIMEOUT_SECONDS)
            except Exception as exc:
                line(False, label, f"{type(exc).__name__}: {exc}")
                continue
            ms = int((time.monotonic() - started) * 1000)
            if result.status.value == "errored":
                line(False, label, f"sortie inexploitable : {result.note}  ({ms}ms)")
                continue
            found = bool(result.findings)
            plausible = found == expect_issue
            line(True, label, f"JSON valide, {len(result.findings)} constat(s), {ms}ms, {result.note}"
                 + ("" if plausible else "   ⚠ verdict peu plausible pour ce modèle"))
    print("\nSetup valide." if not failures else f"\n{failures} problème(s).")
    return 1 if failures else 0


# ──────────────────────────────────────── chat (ISO) ───────────────────────────────────

async def cmd_chat(args) -> int:
    cfg = _prepare(args)
    from app.database import SessionLocal, init_db
    from app.models import Message
    from app.services.llm_service import LLMService
    from app.services.verification import StreamEvent
    from lab import trace
    from lab.display import format_verdict
    from lab.seed import seed

    if args.trace:
        trace.install(show_text=not args.trace_brief)

    init_db()
    db = SessionLocal()
    user_id, conversation_id = seed(db, cfg, continue_last=args.cont)

    started = time.monotonic()
    ttft = None
    verdict = None
    try:
        # exactement l'appel de routes/chat.py
        async for item in LLMService(db).stream_chat(
            user_id=user_id, conversation_id=conversation_id, message=args.question,
            verify_mode=args.mode, emit_events=True, reasoning_mode=None,
        ):
            if isinstance(item, str):
                if ttft is None:
                    ttft = int((time.monotonic() - started) * 1000)
                sys.stdout.write(item)
                sys.stdout.flush()
            elif isinstance(item, StreamEvent):
                if item.kind == "status":
                    _err(f"\n· [{time.monotonic() - started:6.2f}s] {item.data['phase']} (round {item.data['round']})")
                elif item.kind == "verdict":
                    verdict = item.data.to_dict()
    except Exception as exc:
        _err(f"\nERREUR (propagée comme en prod) : {type(exc).__name__}: {exc}")
        return 1

    sys.stdout.write("\n")
    total = int((time.monotonic() - started) * 1000)
    _err("\n" + format_verdict(verdict))
    _err(f"timing: ttft={ttft}ms total={total}ms")

    saved = (
        db.query(Message).filter(Message.conversation_id == conversation_id, Message.role == "assistant")
        .order_by(Message.created_at.desc()).first()
    )
    if saved is not None:
        persisted = (saved.verification or {}).get("status", "∅")
        _err(f"persisté : message {saved.id}  tokens≈{saved.tokens_used}  verification.status={persisted}  "
             f"(conversation {conversation_id}, base {os.environ.get('DATABASE_URL', 'sqlite:///./lab.db')})")
    else:
        _err("persisté : AUCUN message assistant en base")
    return 0


# ──────────────────────────────────────── verify ───────────────────────────────────────

async def cmd_verify(args) -> int:
    cfg = _prepare(args)
    from app.config import get_settings
    from app.services.provider_factory import ProviderFactory
    from app.services.verification import VerificationContext, make_complete, pipeline_from_settings
    from lab import trace
    from lab.display import format_verdict

    if args.trace:
        trace.install(show_text=not args.trace_brief)
    settings = get_settings()

    if args.response in (None, "-"):
        response = sys.stdin.read()
    else:
        with open(args.response, encoding="utf-8") as fh:
            response = fh.read()
    chunks = []
    if args.chunks:
        with open(args.chunks, encoding="utf-8") as fh:
            chunks = json.load(fh)
    schema = None
    if args.schema:
        with open(args.schema, encoding="utf-8") as fh:
            schema = json.load(fh)

    complete = None
    if settings.VERIFY_JUDGE != "off":
        provider = ProviderFactory.create_provider(cfg.provider, api_key=cfg.api_key, base_url=cfg.base_url, config={})
        complete = make_complete(provider, settings.VERIFIER_MODEL or cfg.model)
    pipeline = pipeline_from_settings(settings, is_rag=bool(chunks), judge_complete=complete)
    ctx = VerificationContext(question=args.question, is_rag=bool(chunks), retrieved_chunks=chunks,
                              expected_format=args.format, expected_schema=schema)
    verdict = await pipeline.run(response, ctx)
    print(json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2) if args.json else format_verdict(verdict.to_dict()))
    return 0 if verdict.passed else 2


def build_parser():
    parser = argparse.ArgumentParser(prog="lab", description="Banc d'essai ISO du LLMJudge d'AgentRAG (modèles locaux)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ping", help="valider le setup (provider, génération, juge, ruff, base)")
    _common(p)
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("chat", help="ISO AgentRAG : LLMService.stream_chat réel + persistance")
    _common(p)
    p.add_argument("question")
    p.add_argument("--continue", dest="cont", action="store_true", help="reprendre la dernière conversation (historique)")
    p.add_argument("--trace", action="store_true", help="afficher chaque appel LLM (générateur ET juge) : prompt + sortie")
    p.add_argument("--trace-brief", action="store_true", help="avec --trace : tailles et durées seulement")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("verify", help="vérifier un texte déjà écrit (sans génération)")
    _common(p)
    p.add_argument("--question", default="")
    p.add_argument("--response", default="-", help="fichier (défaut : stdin)")
    p.add_argument("--chunks", help="JSON [{text, metadata, score}] → mode RAG")
    p.add_argument("--format", choices=["json", "yaml"])
    p.add_argument("--schema", help="JSON Schema (sous-ensemble) attendu")
    p.add_argument("--trace", action="store_true")
    p.add_argument("--trace-brief", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
