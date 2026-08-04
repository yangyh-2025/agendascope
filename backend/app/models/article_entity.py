"""article_entities 表：实体-文章显式关联。"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class ArticleEntity(Base):
    __tablename__ = "article_entities"

    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons_orgs.id", ondelete="CASCADE"), primary_key=True
    )
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_offset: Mapped[int | None] = mapped_column(Integer)
    sentiment_towards: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    is_primary_subject: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extracted_by: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("extracted_by IN ('ner','llm','seed_match','manual')", name="ck_ae_extracted_by"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_ae_confidence"),
    )
