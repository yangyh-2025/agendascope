"""topics / topic_articles 表（L2 议题层，v3.0 重构）。

变更点：
- 删除大 JSONB 字段：keywords / country_scope / revision_log / no_merge_with
- keywords → topic_keywords 表
- country_scope → topic_countries 表
- revision_log → topic_lifecycle_events 表
- no_merge_with → topic_no_merge_pairs 表

**注意**：当前 model 仍保留旧 JSONB 字段作为过渡，由旧 worker 写入；
新 API 全部读新维度表。后续 worker 重写时再删除旧字段（alembic 0021）。
"""
import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
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
    llm_model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    cluster_method: Mapped[str] = mapped_column(String(20), nullable=False, default="agglomerative")
    centroid = mapped_column(Vector(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="emerging")
    lifecycle_state: Mapped[str] = mapped_column(String(15), nullable=False, default="nascent")
    confidence: Mapped[str] = mapped_column(String(15), nullable=False, default="watching")
    merged_into: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("topics.id"))
    human_locked_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 过渡期保留的旧 JSONB 字段（worker 继续写，新 API 不读）
    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    country_scope: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    revision_log: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    no_merge_with: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
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
        UUID(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, default=1.0)
    assign_method: Mapped[str] = mapped_column(String(15), nullable=False, default="online")
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("assign_method IN ('online','recluster','merge','manual')", name="ck_ta_assign_method"),
    )
