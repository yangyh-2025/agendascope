"""agenda_events / agenda_event_evidence 表（L2 事件层，v3.0 重构）。

变更点：
- 删 follower_sequence JSONB → agenda_event_followers 表
- 删 stats_evidence JSONB（数据迁移到 topic_snapshots / 主表字段）
- 删 revision_log JSONB → 由 topic_lifecycle_events 承接（事件级复用 topic）
- 新增 subject_entity_id / object_entity_id（GDELT Actor1/Actor2 风格，可空）
"""
import uuid
from datetime import datetime

from sqlalchemy import CHAR, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class AgendaEvent(Base):
    __tablename__ = "agenda_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id"), nullable=False
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="watching")
    confidence: Mapped[str] = mapped_column(String(12), nullable=False, default="watching")
    origin_type: Mapped[str] = mapped_column(String(10), nullable=False)
    origin_country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    origin_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id")
    )
    origin_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons_orgs.id")
    )
    origin_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    origin_confidence: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    origin_quote: Mapped[str | None] = mapped_column(Text)
    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons_orgs.id")
    )
    object_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons_orgs.id")
    )
    # 过渡期保留的旧 JSONB 字段（worker 继续写，新 API 不读）
    follower_sequence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    stats_evidence: Mapped[dict | None] = mapped_column(JSONB)
    revision_log: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    detection_method: Mapped[str] = mapped_column(String(20), nullable=False, default="llm")
    final_review: Mapped[dict | None] = mapped_column(JSONB)
    human_locked_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismiss_reason: Mapped[str | None] = mapped_column(Text)
    is_false_positive: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('watching','suspected','confirmed','dismissed','revised','archived')",
            name="ck_events_status",
        ),
        CheckConstraint("confidence IN ('watching','suspected','confirmed')", name="ck_events_confidence"),
        CheckConstraint("origin_type IN ('media','person','org')", name="ck_events_origin_type"),
        CheckConstraint("origin_confidence IN ('high','medium','low')", name="ck_events_origin_confidence"),
        CheckConstraint("detection_method IN ('llm','media_time_fallback')", name="ck_events_detection_method"),
    )


class AgendaEventEvidence(Base):
    __tablename__ = "agenda_event_evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agenda_events.id", ondelete="CASCADE"), nullable=False
    )
    evidence_type: Mapped[str] = mapped_column(String(20), nullable=False)
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id")
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons_orgs.id")
    )
    quote: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('origin_article','origin_utterance','follower_article','stat_snapshot')",
            name="ck_evidence_type",
        ),
    )
