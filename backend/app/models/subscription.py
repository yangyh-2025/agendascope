"""subscriptions / subscription_deliveries 表（T4.16 订阅推送，详细设计 1.10 扩展）。

- subscriptions：用户 × 国家 × 议题分类 的日报/周报订阅；unsubscribe_token 支撑免登录一键退订
- subscription_deliveries：每期投递记录（退避重试队列 + 日终失败报告数据源）
"""
import secrets
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


def _gen_unsubscribe_token() -> str:
    return secrets.token_urlsafe(32)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    country_codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    topic_category: Mapped[str | None] = mapped_column(String(50))  # None = 全部议题分类
    frequency: Mapped[str] = mapped_column(String(10), nullable=False, default="daily")
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="zh-CN")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    unsubscribe_token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, default=_gen_unsubscribe_token)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("frequency IN ('daily','weekly')", name="ck_subscriptions_frequency"),
    )


class SubscriptionDelivery(Base):
    __tablename__ = "subscription_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(10), nullable=False)  # daily/weekly（冗余便于日终报告）
    period_date: Mapped[date] = mapped_column(Date, nullable=False)  # 本期投递归属日
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending','sent','failed')", name="ck_deliveries_status"),
        UniqueConstraint("subscription_id", "period", "period_date", name="uq_delivery_scope"),
    )
