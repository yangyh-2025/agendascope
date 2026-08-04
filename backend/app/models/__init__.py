"""ORM 模型总入口（Alembic autogenerate 与仓库层共用 metadata）。"""
from app.db.session import Base  # noqa: F401
from app.models import (  # noqa: F401
    agenda,
    agenda_event_dimensions,
    alert,
    api_key,
    article,
    article_entity,
    audit,
    collection,
    entity_relation,
    llm,
    person,
    processing,
    snapshots,
    source,
    subscription,
    system_state,
    topic,
    topic_dimensions,
    user,
)
