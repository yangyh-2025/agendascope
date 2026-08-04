"""entity_relations / relation_evidences 表：监控对象社交网络（详细设计 9.1）。

- entity_relations: 主体-客体-关系类型三元组，含置信度（带时间衰减）
- relation_evidences: 每条关系的支撑新闻证据（LLM 从正文抽出的 evidence_quote）
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class EntityRelation(Base):
    __tablename__ = "entity_relations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons_orgs.id", ondelete="CASCADE"), nullable=False
    )
    object_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons_orgs.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, default=0.5)
    base_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, default=0.5)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "subject_entity_id", "object_entity_id", "relation_type",
            name="uq_er_subject_object_type",
        ),
        CheckConstraint(
            "relation_type IN ('meets','sanctions','appoints','criticizes','supports','opposes',"
            "'allies_with','member_of','advises','funds','invests_in','signals_support',"
            "'travelled_to','statement_about','family_of','other')",
            name="ck_er_relation_type",
        ),
        CheckConstraint("status IN ('active','expired','rejected')", name="ck_er_status"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_er_confidence"),
        CheckConstraint("base_confidence BETWEEN 0 AND 1", name="ck_er_base_confidence"),
    )


class RelationEvidence(Base):
    __tablename__ = "relation_evidences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    relation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_relations.id", ondelete="CASCADE"), nullable=False
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    evidence_quote: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_quote_zh: Mapped[str | None] = mapped_column(Text)
    context_paragraph: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    llm_judgement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_judgements.id")
    )

    __table_args__ = (
        UniqueConstraint("relation_id", "article_id", name="uq_evidence_relation_article"),
    )
