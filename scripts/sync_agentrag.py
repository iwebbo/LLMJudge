import argparse
import pathlib
import sys

LAB = pathlib.Path(__file__).resolve().parent.parent

ISO_FILES = ["app/services/llm_service.py", "app/services/verification_service.py", "app/services/provider_factory.py"]
ISO_DIRS = ["app/services/verification", "tests/verification"]
TO_DIRS = ISO_DIRS  # ce qu'on a le droit de renvoyer vers AgentRAG

PATCH_OLD = """import openai
from anthropic import AsyncAnthropic
import httpx
import asyncio
import time
from google.generativeai import GenerativeModel 
from google.generativeai.types import GenerationConfig
import google.generativeai as genai 
from huggingface_hub import AsyncInferenceClient
"""
PATCH_NEW = """import openai
import httpx
import asyncio
import time

# LAB PATCH (seule modification du fichier) : SDK cloud optionnels, inutiles pour les modèles locaux.
# Utilisés uniquement si on instancie claude / gemini / huggingface.
try:
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover
    AsyncAnthropic = None
try:
    from google.generativeai import GenerativeModel
    from google.generativeai.types import GenerationConfig
    import google.generativeai as genai
except ImportError:  # pragma: no cover
    GenerativeModel = GenerationConfig = genai = None
try:
    from huggingface_hub import AsyncInferenceClient
except ImportError:  # pragma: no cover
    AsyncInferenceClient = None
"""


def read(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def transform(rel, text, direction):
    if rel.endswith("provider_factory.py"):
        return text.replace(PATCH_OLD, PATCH_NEW) if direction == "from" else text.replace(PATCH_NEW, PATCH_OLD)
    return text


def pairs(backend, direction):
    items = []
    if direction != "to":
        for rel in ISO_FILES:
            items.append((rel, backend / rel, LAB / rel))
    for rel in (TO_DIRS if direction == "to" else ISO_DIRS):
        src_dir = (LAB if direction == "to" else backend) / rel
        for src in sorted(src_dir.glob("*.py")):
            items.append((f"{rel}/{src.name}", backend / rel / src.name, LAB / rel / src.name))
    return items


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--from", dest="frm")
    g.add_argument("--to")
    g.add_argument("--check")
    args = p.parse_args()
    direction = "from" if args.frm else "to" if args.to else "check"
    backend = pathlib.Path(args.frm or args.to or args.check)
    changed = 0
    for rel, app_path, lab_path in pairs(backend, "from" if direction == "check" else direction):
        src, dst = (lab_path, app_path) if direction == "to" else (app_path, lab_path)
        if not src.exists():
            continue
        new = transform(rel, read(src), "to" if direction == "to" else "from")
        old = read(dst) if dst.exists() else None
        if new != old:
            changed += 1
            print(("différent : " if direction == "check" else "mis à jour : ") + rel)
            if direction != "check":
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(new, encoding="utf-8", newline="\n")
    print(f"{changed} fichier(s) {'différent(s)' if direction == 'check' else 'modifié(s)'}" if changed else "synchronisé")
    return 1 if (direction == "check" and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
