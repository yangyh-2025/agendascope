"""articles 表（详细设计 2.6）。"""
import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import CHAR, Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    url_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    title_translated: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    language_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_source: Mapped[str] = mapped_column(String(10), nullable=False, default="feed")
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    visible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_status: Mapped[str] = mapped_column(String(10), nullable=False, default="full")
    sentiment: Mapped[str | None] = mapped_column(String(10))
    sentiment_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    embedding = mapped_column(Vector(768), nullable=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canonical_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id")
    )
    source_channel: Mapped[str] = mapped_column(String(10), nullable=False, default="rss")
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("time_source IN ('feed','crawled','gdelt')", name="ck_articles_time_source"),
        CheckConstraint("content_status IN ('full','partial','failed')", name="ck_articles_content_status"),
        CheckConstraint("sentiment IN ('positive','neutral','negative')", name="ck_articles_sentiment"),
        CheckConstraint("sentiment_score BETWEEN -1 AND 1", name="ck_articles_sentiment_score"),
        CheckConstraint("source_channel IN ('rss','rsshub','gdelt')", name="ck_articles_source_channel"),
    )
