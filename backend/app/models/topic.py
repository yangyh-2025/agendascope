"""topics / topic_articles / agenda_snapshots 表（详细设计 2.7/2.8/2.9）。"""
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    name_auto: Mapped[str] = mapped_column(String(300), nullable=False)
    name_zh: Mapped[str | None] = mapped_column(String(300))
    topic_category: Mapped[str | None] = mapped_column(String(50))
    summary_zh: Mapped[str | None] = mapped_column(Text)
    naming_method: Mapped[str] = mapped_column(String(20), nullable=False, default="llm")
    llm_model: Mapped[str | None] = mapped_column(String(100))  # 最近一次 LLM 判定所用模型名（T2.17 留痕）
    prompt_version: Mapped[str | None] = mapped_column(String(50))  # 最近一次 LLM 判定所用 prompt 版本
    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    cluster_method: Mapped[str] = mapped_column(String(20), nullable=False, default="bertopic")
    centroid = mapped_column(Vector(1024), nullable=True)
    country_scope: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="emerging")
    lifecycle_state: Mapped[str] = mapped_column(String(15), nullable=False, default="nascent")
    confidence: Mapped[str] = mapped_column(String(15), nullable=False, default="watching")
    merged_into: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("topics.id"))
    no_merge_with: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    revision_log: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    human_locked_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("naming_method IN ('llm','ctfidf_fallback','keyword_fallback')", name="ck_topics_naming_method"),
        CheckConstraint("cluster_method IN ('bertopic','agglomerative','keyword_fallback')", name="ck_topics_cluster_method"),
        CheckConstraint("status IN ('emerging','heating','stable','declining','archived')", name="ck_topics_status"),
        CheckConstraint(
            "lifecycle_state IN ('nascent','forming','confirmed','evolving','archived')",
            name="ck_topics_lifecycle_state",
        ),
        CheckConstraint("confidence IN ('watching','suspected','confirmed')", name="ck_topics_confidence"),
    )


class TopicArticle(Base):
    __tablename__ = "topic_articles"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id"), primary_key=True
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id"), primary_key=True
    )
    weight: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=1.0)
    assign_method: Mapped[str] = mapped_column(String(15), nullable=False, default="online")
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("assign_method IN ('online','recluster','merge','manual')", name="ck_ta_assign_method"),
    )


class AgendaSnapshot(Base):
    __tablename__ = "agenda_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granularity: Mapped[str] = mapped_column(String(5), nullable=False, default="day")
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    salience_score: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    salience_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    sentiment_pos: Mapped[float | None] = mapped_column(Numeric(5, 4))
    sentiment_neu: Mapped[float | None] = mapped_column(Numeric(5, 4))
    sentiment_neg: Mapped[float | None] = mapped_column(Numeric(5, 4))
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
