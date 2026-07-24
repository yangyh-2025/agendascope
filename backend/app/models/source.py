"""sources 表（详细设计 2.4）。"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CHAR, CheckConstraint, DateTime, Integer, SmallInteger, String, Numeric
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_zh: Mapped[str | None] = mapped_column(String(200))
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    homepage_url: Mapped[str] = mapped_column(String(500), nullable=False)
    feed_url: Mapped[str | None] = mapped_column(String(500))
    collect_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="rss")
    adapter_type: Mapped[str] = mapped_column(String(10), nullable=False, default="rss")
    crawl_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    poll_interval_min: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)
    audience_weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    coverage_confidence: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    is_custom: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    degraded_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("collect_mode IN ('rss','rsshub','gdelt')", name="ck_sources_collect_mode"),
        CheckConstraint("adapter_type IN ('rss','pipeline')", name="ck_sources_adapter_type"),
        CheckConstraint("media_type IN ('newspaper','agency','broadcast','online')", name="ck_sources_media_type"),
        CheckConstraint("poll_interval_min BETWEEN 1 AND 60", name="ck_sources_poll_interval"),
        CheckConstraint("audience_weight BETWEEN 0 AND 100", name="ck_sources_audience_weight"),
        CheckConstraint("coverage_confidence IN ('high','medium','low')", name="ck_sources_coverage_confidence"),
        CheckConstraint("status IN ('active','degraded','failed')", name="ck_sources_status"),
    )
