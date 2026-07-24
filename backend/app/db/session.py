"""数据库引擎与会话。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def init_engine(database_url: str | None = None):
    global _engine, _SessionLocal
    url = database_url or get_settings().database_url
    _engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_engine():
    global _engine
    if _engine is None:
        init_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal


def get_db():
    """FastAPI 依赖：每请求一个会话。"""
    db: Session = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
