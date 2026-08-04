"""article_processing / worker_tasks 表（L1 加工层）。"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class ArticleProcessing(Base):
    """每篇文章的加工流水账。各 worker 按状态机领取任务。"""

    __tablename__ = "article_processing"

    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    nlp_status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    nlp_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nlp_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nlp_error: Mapped[str | None] = mapped_column(Text)
    cluster_status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    cluster_assigned_topic_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    cluster_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cluster_similarity: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    entity_extract_status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    entity_extract_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    relation_extract_status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    relation_extract_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    translate_status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("nlp_status IN ('pending','processing','done','failed','skipped')", name="ck_ap_nlp"),
        CheckConstraint("cluster_status IN ('pending','processing','done','failed','skipped')", name="ck_ap_cluster"),
        CheckConstraint("entity_extract_status IN ('pending','processing','done','failed','skipped')", name="ck_ap_entity"),
        CheckConstraint("relation_extract_status IN ('pending','processing','done','failed','skipped')", name="ck_ap_relation"),
        CheckConstraint("translate_status IN ('pending','processing','done','failed','skipped','not_needed')", name="ck_ap_translate"),
    )


class WorkerTask(Base):
    """分布式 worker 任务队列。当前仅建表，未来启用分布式消费。"""

    __tablename__ = "worker_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="pending")
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "task_type IN ('nlp_embed','cluster_assign','entity_extract','relation_extract','translate','collect','snapshot','detect')",
            name="ck_wt_task_type",
        ),
        CheckConstraint("status IN ('pending','claimed','done','failed','expired')", name="ck_wt_status"),
    )
