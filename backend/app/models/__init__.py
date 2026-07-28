"""ORM 模型总入口（Alembic autogenerate 与仓库层共用 metadata）。"""
from app.db.session import Base  # noqa: F401
from app.models import (  # noqa: F401
    agenda,
    alert,
    article,
    audit,
    collection,
    llm,
    person,
    report,
    source,
    subscription,
    topic,
    user,
)
