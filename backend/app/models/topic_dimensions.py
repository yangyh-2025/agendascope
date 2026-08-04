"""topic_keywords / topic_countries / topic_lifecycle_events / topic_no_merge_pairs 表。"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class TopicKeyword(Base):
    __tablename__ = "topic_keywords"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    keyword: Mapped[str] = mapped_column(String(100), nullable=False, primary_key=True)
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=1.0)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="ctfidf")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("source IN ('ctfidf','llm','manual','imported')", name="ck_tk_source"),
    )


class TopicCountry(Base):
    __tablename__ = "topic_countries"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    country_code: Mapped[str] = mapped_column(CHAR(2), primary_key=True)
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    salience_peak: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))


class TopicLifecycleEvent(Base):
    __tablename__ = "topic_lifecycle_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    from_value: Mapped[dict | None] = mapped_column(JSONB)
    to_value: Mapped[dict | None] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('created','lifecycle_change','status_change','merged','split','renamed','origin_revised','locked','unlocked')",
            name="ck_tle_event_type",
        ),
        CheckConstraint(
            "actor IN ('system','nlp_worker','cluster_worker','detection_worker','relation_worker','snapshot_worker','llm','human')",
            name="ck_tle_actor",
        ),
    )


class TopicNoMergePair(Base):
    __tablename__ = "topic_no_merge_pairs"

    topic_id_a: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id_b: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
