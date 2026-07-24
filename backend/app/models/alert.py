"""alert_rules / alerts 表（详细设计 2.13）。"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    country_codes: Mapped[list] = mapped_column(JSONB, nullable=False)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id")
    )
    keywords: Mapped[list | None] = mapped_column(JSONB)
    condition_type: Mapped[str] = mapped_column(String(15), nullable=False)
    condition_value: Mapped[float] = mapped_column(Numeric, nullable=False)
    condition_extra: Mapped[dict | None] = mapped_column(JSONB)
    active_period: Mapped[str] = mapped_column(String(10), nullable=False, default="all_day")
    active_hours: Mapped[dict | None] = mapped_column(JSONB)
    notify_channels: Mapped[list] = mapped_column(JSONB, nullable=False, default=lambda: ["inapp", "email"])
    webhook_url: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("condition_type IN ('growth_rate','top_n','neg_ratio')", name="ck_rules_condition_type"),
        CheckConstraint("active_period IN ('all_day','custom')", name="ck_rules_active_period"),
        CheckConstraint("topic_id IS NOT NULL OR keywords IS NOT NULL", name="ck_rules_target"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alert_rules.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="unread")
    suppressed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notify_result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('unread','read','archived','suppressed')", name="ck_alerts_status"),
    )
