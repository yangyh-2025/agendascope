"""report_exports 表（详细设计 2.14）。"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class ReportExport(Base):
    __tablename__ = "report_exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    template: Mapped[str] = mapped_column(String(20), nullable=False)
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="zh-CN")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="processing")
    file_path: Mapped[str | None] = mapped_column(String(500))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    watermark: Mapped[str] = mapped_column(String(200), nullable=False, default="由 AgendaScope 观澜生成 + 数据口径声明")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("template IN ('topic_deep','compare_brief','periodic_weekly')", name="ck_exports_template"),
        CheckConstraint("format IN ('pdf','docx','markdown','csv')", name="ck_exports_format"),
        CheckConstraint("status IN ('processing','done','failed')", name="ck_exports_status"),
    )
