"""persons_orgs 表（详细设计 2.12）。"""
import uuid
from datetime import datetime

from sqlalchemy import CHAR, Boolean, CheckConstraint, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class PersonOrg(Base):
    __tablename__ = "persons_orgs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(15), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_zh: Mapped[str | None] = mapped_column(String(200))
    name_aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    role_title: Mapped[str | None] = mapped_column(String(200))
    monitored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_seed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    category: Mapped[str | None] = mapped_column(String(50))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_utterances: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("entity_type IN ('person','thinktank','intl_org','gov_body')", name="ck_po_entity_type"),
    )
