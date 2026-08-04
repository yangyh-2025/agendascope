"""topic_snapshots / entity_snapshots / source_snapshots 表（L3 快照层）。

topic_snapshots 替代旧 agenda_snapshots（命名对齐）。
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class TopicSnapshot(Base):
    __tablename__ = "topic_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granularity: Mapped[str] = mapped_column(String(5), nullable=False)
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    salience_score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    salience_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    sentiment_pos: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    sentiment_neu: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    sentiment_neg: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    top_attributes: Mapped[dict | None] = mapped_column(JSONB)
    network_metrics: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("window_end > window_start", name="ck_snap_window"),
        CheckConstraint("granularity IN ('hour','day','week')", name="ck_snap_granularity"),
        CheckConstraint("article_count >= 0", name="ck_snap_article_count"),
        CheckConstraint("salience_rank >= 1", name="ck_snap_rank"),
        UniqueConstraint("country_code", "topic_id", "window_start", "granularity", name="uq_snap_scope"),
    )


class EntitySnapshot(Base):
    __tablename__ = "entity_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons_orgs.id", ondelete="CASCADE"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granularity: Mapped[str] = mapped_column(String(5), nullable=False)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sentiment_avg: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    sentiment_pos: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    sentiment_neg: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    first_utterance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    relation_new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top_topics: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("window_end > window_start", name="ck_es_window"),
        CheckConstraint("granularity IN ('hour','day','week')", name="ck_es_granularity"),
        UniqueConstraint("entity_id", "window_start", "granularity", name="uq_es_scope"),
    )


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granularity: Mapped[str] = mapped_column(String(5), nullable=False)
    articles_published: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    articles_collected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_utterance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    follow_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_lag_seconds: Mapped[int | None] = mapped_column(Integer)
    collection_success_rate: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("window_end > window_start", name="ck_ss_window"),
        CheckConstraint("granularity IN ('hour','day','week')", name="ck_ss_granularity"),
        UniqueConstraint("source_id", "window_start", "granularity", name="uq_ss_scope"),
    )
