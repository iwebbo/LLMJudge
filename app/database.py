from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, echo=settings.DB_ECHO, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)  # mêmes options qu'AgentRAG
Base = declarative_base()


def init_db():
    import app.models  # noqa: F401  (enregistre les tables)

    Base.metadata.create_all(bind=engine)
