"""订阅推送服务（T4.16）：国家 × 议题分类 日报/周报。

流程（由 alerting_worker 周期调用 run_subscription_round）：
  1. 到期订阅生成当期 delivery（pending，唯一约束防重复）
  2. 发送 pending delivery（议题名/摘要经 translate.py 离线翻译，翻译失效显示原文不阻塞）
  3. 失败 delivery 指数退避重试（复用 notifier.RETRY_BACKOFF_SECONDS 1m/5m/15m）
  4. 重试耗尽 → 日终失败报告：聚合写 alerts 给管理员（reported 标记防重复上报）

发送走 notifier 邮件通道；用户无邮箱/SMTP 未配置时记 failed 并注明原因（不伪造成功）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerting import notifier
from app.alerting.llm_translate import llm_translate
from app.alerting.translate import translate_summary, translate_topic_name
from app.core.logging import get_logger
from app.models.subscription import Subscription, SubscriptionDelivery
from app.models.topic import AgendaSnapshot, Topic
from app.models.user import User

logger = get_logger("alerting.subscription")

_DIGEST_TOP_N = 10
_PERIOD_DAYS = {"daily": 1, "weekly": 7}
_MAX_SCAN = 500


# ---------------------------------------------------------------------------
# 到期判定
# ---------------------------------------------------------------------------


def is_due(sub: Subscription, now: datetime) -> bool:
    """daily：今日未发过；weekly：距上次发送 ≥7 天。"""
    if sub.last_sent_at is None:
        return True
    last = sub.last_sent_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if sub.frequency == "weekly":
        return (now - last) >= timedelta(days=7)
    return last.date() < now.date()


# ---------------------------------------------------------------------------
# 摘要内容生成（真实查询 topics/snapshots）
# ---------------------------------------------------------------------------


def build_digest(
    db: Session,
    sub: Subscription,
    now: datetime,
    translate_cfg=None,
    http_client=None,
    llm_annotator=None,
) -> dict[str, Any]:
    """按订阅范围（国家 × 议题分类）聚合近 daily/weekly 窗口的 Top 议题。

    议题名/摘要按 sub.locale 处理：
    - zh 订阅：摘要优先用 LLM 翻译（llm_translate），LLM 不可用时回落原文；
      议题名直接用 name_zh（已是中文，不强行 argos 英→中避免质量差）。
    - 非 zh 订阅：维持 argos 离线翻译（translate.py 内部失效回退原文）。
    """
    days = _PERIOD_DAYS.get(sub.frequency, 1)
    window_start = now - timedelta(days=days)

    stmt = (
        select(AgendaSnapshot, Topic)
        .join(Topic, Topic.id == AgendaSnapshot.topic_id)
        .where(AgendaSnapshot.window_end >= window_start)
        .order_by(AgendaSnapshot.salience_rank.asc())
    )
    if sub.country_codes:
        stmt = stmt.where(AgendaSnapshot.country_code.in_(sub.country_codes))
    if sub.topic_category:
        stmt = stmt.where(Topic.topic_category == sub.topic_category)

    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    target_lang = (sub.locale or "zh-CN").split("-")[0].lower()
    for snap, topic in db.execute(stmt).all():
        key = (snap.country_code, str(topic.id))
        if key in seen:
            continue
        seen.add(key)
        if len(items) >= _DIGEST_TOP_N:
            break
        # 摘要翻译：zh 订阅优先 LLM（llm_translate，不可用回落原文）；
        # 非 zh 订阅维持 argos 离线翻译（内部失效回退原文）。
        summary = topic.summary_zh or ""
        if summary:
            if target_lang == "zh":
                summary = llm_translate(db, summary, target_lang="zh", llm_annotator=llm_annotator)
            else:
                summary = translate_summary(
                    summary, sub.locale,
                    cfg=translate_cfg, http_client=http_client,
                )
        items.append({
            "country_code": snap.country_code,
            "topic_name": translate_topic_name(
                topic.name_zh, topic.name_auto, sub.locale,
                cfg=translate_cfg, http_client=http_client,
            ),
            "summary": summary,
            "article_count": int(snap.article_count),
            "salience_rank": int(snap.salience_rank),
            "sentiment_neg": float(snap.sentiment_neg) if snap.sentiment_neg is not None else None,
        })

    return {
        "frequency": sub.frequency,
        "period_days": days,
        "window_start": window_start.isoformat(),
        "window_end": now.isoformat(),
        "country_codes": list(sub.country_codes or []),
        "topic_category": sub.topic_category,
        "items": items,
    }


def render_digest_text(sub: Subscription, digest: dict[str, Any], unsubscribe_url: str) -> tuple[str, str]:
    """渲染日报/周报邮件标题与正文（含退订链接）。"""
    label = "日报" if sub.frequency == "daily" else "周报"
    scope = ",".join(digest["country_codes"]) or "全球"
    title = f"[AgendaScope] 议题监测{label} - {scope}"
    lines = [
        f"监测范围：{scope}" + (f" / 分类 {digest['topic_category']}" if digest.get("topic_category") else ""),
        f"统计窗口：近 {digest['period_days']} 天（{digest['window_start'][:10]} ~ {digest['window_end'][:10]}）",
        "",
    ]
    if not digest["items"]:
        lines.append("本期监测范围内无达到显著性阈值的议题。")
    for i, item in enumerate(digest["items"], 1):
        lines.append(
            f"{i}. [{item['country_code']}] {item['topic_name']}"
            f"（显著性 #{item['salience_rank']}，报道 {item['article_count']} 篇）"
        )
        if item.get("summary"):
            lines.append(f"   {item['summary']}")
    lines += [
        "",
        "——",
        f"退订本推送：{unsubscribe_url}",
        "本邮件由 AgendaScope 观澜自动生成，数据口径以平台快照为准。",
    ]
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# 投递执行（首发 + 退避重试）
# ---------------------------------------------------------------------------


def _attempt_delivery(
    db: Session,
    delivery: SubscriptionDelivery,
    smtp_config: notifier.SmtpConfig | None,
    now: datetime,
    unsubscribe_base_url: str,
    translate_cfg=None,
    http_client=None,
    llm_annotator=None,
) -> bool:
    """执行一次投递尝试，更新 delivery 状态；返回是否成功。"""
    sub = db.get(Subscription, delivery.subscription_id)
    delivery.attempts = int(delivery.attempts or 0) + 1
    if sub is None or not sub.enabled:
        delivery.status = "failed"
        delivery.error = "subscription_disabled_or_deleted"
        delivery.next_retry_at = None
        return False

    user = db.get(User, sub.user_id)
    if user is None or not user.email:
        delivery.status = "failed"
        delivery.error = "user_email_missing"
        delivery.next_retry_at = None  # 无邮箱重试无意义，直接终态
        return False
    if smtp_config is None:
        delivery.status = "failed"
        delivery.error = "smtp_not_configured"
        delivery.next_retry_at = None
        return False

    digest = build_digest(
        db, sub, now, translate_cfg=translate_cfg, http_client=http_client,
        llm_annotator=llm_annotator,
    )
    unsubscribe_url = f"{unsubscribe_base_url.rstrip('/')}/api/v1/subscriptions/unsubscribe?token={sub.unsubscribe_token}"
    title, body = render_digest_text(sub, digest, unsubscribe_url)
    ok_flag, err = notifier.send_email(smtp_config, user.email, title, body)

    if ok_flag:
        delivery.status = "sent"
        delivery.error = None
        delivery.next_retry_at = None
        delivery.sent_at = now
        sub.last_sent_at = now
        return True

    delivery.status = "failed"
    delivery.error = (err or "send_failed")[:500]
    if delivery.attempts <= len(notifier.RETRY_BACKOFF_SECONDS):
        backoff = notifier.RETRY_BACKOFF_SECONDS[delivery.attempts - 1]
        delivery.next_retry_at = now + timedelta(seconds=backoff)
    else:
        delivery.next_retry_at = None  # 重试耗尽，待日终失败报告
    return False


def run_subscription_round(
    db: Session,
    smtp_config: notifier.SmtpConfig | None = None,
    now: datetime | None = None,
    unsubscribe_base_url: str = "http://localhost:8000",
    translate_cfg=None,
    http_client=None,
    llm_annotator=None,
) -> dict[str, int]:
    """单轮订阅推送：到期生成 → 首发 → 退避重试 → 日终失败报告（alerts 给管理员）。

    llm_annotator 注入时，zh 订阅的摘要用 LLM 翻译（不可用回落原文），不阻塞发送。
    """
    now = now or datetime.now(UTC)
    stats = {"generated": 0, "sent": 0, "retried": 0, "failed_final": 0}

    # 1. 到期订阅生成当期 delivery
    subs = list(db.scalars(
        select(Subscription).where(Subscription.enabled.is_(True)).limit(_MAX_SCAN)
    ).all())
    for sub in subs:
        if not is_due(sub, now):
            continue
        exists = db.scalar(
            select(SubscriptionDelivery).where(
                SubscriptionDelivery.subscription_id == sub.id,
                SubscriptionDelivery.period == sub.frequency,
                SubscriptionDelivery.period_date == now.date(),
            )
        )
        if exists is None:
            db.add(SubscriptionDelivery(
                subscription_id=sub.id, period=sub.frequency, period_date=now.date(),
            ))
            stats["generated"] += 1
    db.flush()

    # 2. 首发 pending + 3. 到期重试
    deliveries = list(db.scalars(
        select(SubscriptionDelivery).where(
            SubscriptionDelivery.status.in_(["pending", "failed"]),
        ).order_by(SubscriptionDelivery.created_at.asc()).limit(_MAX_SCAN)
    ).all())
    for delivery in deliveries:
        if delivery.status == "failed":
            exhausted = delivery.attempts > len(notifier.RETRY_BACKOFF_SECONDS) or delivery.next_retry_at is None
            if exhausted:
                continue  # 终态由下方日终报告处理
            if delivery.next_retry_at and delivery.next_retry_at.replace(tzinfo=UTC) > now:
                continue
            stats["retried"] += 1
        if _attempt_delivery(
            db, delivery, smtp_config, now, unsubscribe_base_url,
            translate_cfg=translate_cfg, http_client=http_client, llm_annotator=llm_annotator,
        ):
            stats["sent"] += 1
    db.flush()

    # 4. 日终失败报告：重试耗尽且未上报 → 聚合写 alerts 给管理员
    failed_final = list(db.scalars(
        select(SubscriptionDelivery).where(
            SubscriptionDelivery.status == "failed",
            SubscriptionDelivery.reported.is_(False),
            SubscriptionDelivery.next_retry_at.is_(None),
        ).limit(_MAX_SCAN)
    ).all())
    if failed_final:
        from app.models.alert import Alert, AlertRule
        from app.services.seed_service import ensure_admin

        admin = ensure_admin(db)
        lines = [
            f"- {d.period} {d.period_date.isoformat()} 订阅 {d.subscription_id}：{d.error}"
            for d in failed_final[:20]
        ]
        rule = db.scalar(select(AlertRule).where(AlertRule.name == "系统-订阅投递失败报告"))
        if rule is None:
            rule = AlertRule(
                user_id=admin.id,
                name="系统-订阅投递失败报告",
                country_codes=[],
                keywords=["__subscription_delivery__"],
                condition_type="growth_rate",
                condition_value=0,
                notify_channels=["inapp"],
            )
            db.add(rule)
            db.flush()
        alert = Alert(
            rule_id=rule.id,
            user_id=admin.id,
            payload={
                "kind": "subscription_delivery_failure_report",
                "message": (
                    f"本日 {len(failed_final)} 条订阅推送重试耗尽仍失败：\n" + "\n".join(lines)
                ),
                "failed_count": len(failed_final),
                "report_date": now.date().isoformat(),
            },
            status="unread",
        )
        db.add(alert)
        for d in failed_final:
            d.reported = True
        stats["failed_final"] = len(failed_final)
        logger.error(
            "subscription_delivery_failure_report",
            failed_count=len(failed_final),
        )
    db.flush()
    return stats


__all__ = [
    "build_digest",
    "is_due",
    "render_digest_text",
    "run_subscription_round",
]
