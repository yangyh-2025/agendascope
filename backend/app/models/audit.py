"""audit_logs 表（详细设计 2.15，只增不改）。"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    username: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(300))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    result: Mapped[str] = mapped_column(String(10), nullable=False, default="success")

    __table_args__ = (
        CheckConstraint("result IN ('success','failure','denied')", name="ck_audit_result"),
    )
