"""collection_jobs 表（详细设计 2.5，治理状态机六态）。"""
import uuid
from datetime import datetime

from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base

# 治理状态机六态（详细设计 2.5 COMMENT，大写为例外命名）
JOB_PENDING = "PENDING"
JOB_RUNNING = "RUNNING"
JOB_SUCCESS = "SUCCESS"
JOB_TEMP_FAIL = "TEMP_FAIL"
JOB_PERM_FAIL = "PERM_FAIL"
JOB_SKIPPED = "SKIPPED"
JOB_STATES = (JOB_PENDING, JOB_RUNNING, JOB_SUCCESS, JOB_TEMP_FAIL, JOB_PERM_FAIL, JOB_SKIPPED)


class CollectionJob(Base):
    __tablename__ = "collection_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(15), nullable=False, default=JOB_PENDING)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    articles_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    articles_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    latency_stats: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("channel IN ('rss','rsshub','gdelt')", name="ck_jobs_channel"),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCESS','TEMP_FAIL','PERM_FAIL','SKIPPED')",
            name="ck_jobs_status",
        ),
    )
