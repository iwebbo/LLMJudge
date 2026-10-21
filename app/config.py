import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name, default):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_dotenv(path=".env"):
    """Chargeur .env minimal ; l'environnement réel reste prioritaire."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.split(" #")[0].strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


@dataclass
class Settings:
    DATABASE_URL: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./lab.db"))
    DB_ECHO: bool = field(default_factory=lambda: _bool(_env("DB_ECHO", "false")))

    VERIFY_MODE: str = field(default_factory=lambda: _env("VERIFY_MODE", "annotate"))
    VERIFY_JUDGE: str = field(default_factory=lambda: _env("VERIFY_JUDGE", "auto"))
    VERIFY_RUFF: bool = field(default_factory=lambda: _bool(_env("VERIFY_RUFF", "true")))
    VERIFY_MAX_ROUNDS: int = field(default_factory=lambda: int(_env("VERIFY_MAX_ROUNDS", 2)))
    VERIFY_MIN_SCORE: float = field(default_factory=lambda: float(_env("VERIFY_MIN_SCORE", 0.7)))
    VERIFY_TIMEOUT_SECONDS: int = field(default_factory=lambda: int(_env("VERIFY_TIMEOUT_SECONDS", 120)))
    VERIFY_KEEPALIVE_SECONDS: int = field(default_factory=lambda: int(_env("VERIFY_KEEPALIVE_SECONDS", 10)))
    VERIFIER_PROVIDER_NAME: str = field(default_factory=lambda: _env("VERIFIER_PROVIDER_NAME", ""))
    VERIFIER_MODEL: str = field(default_factory=lambda: _env("VERIFIER_MODEL", ""))
    RAG_MIN_SCORE: float = field(default_factory=lambda: float(_env("RAG_MIN_SCORE", 0.25)))


@lru_cache()
def get_settings() -> Settings:
    load_dotenv()
    return Settings()
