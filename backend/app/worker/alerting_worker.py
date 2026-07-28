"""预警调度 worker 进程入口：python -m app.worker.alerting_worker（T4.14/T4.15）。

职责：
  ① 周期评估（默认 15 min，与 agenda_snapshots 刷新对齐）：
     alerting.engine.run_evaluation_round 全量评估 enabled 规则，
     notify_hook 走 notifier.notify_alert（站内/邮件/Webhook）；
  ② 通知退避重试执行者：notifier 首发失败仅写 notify_result.next_retry_at，
     本 worker 每轮扫描到期项执行重试（指数退避 1m/5m/15m 共 3 次）；
     Webhook 3 次仍失败 → 降级邮件 + 规则层面停用 webhook 通道（落库）+ 站内告警；
  ③ 订阅推送（T4.16）：日报/周报到期生成投递 + 投递失败退避重试 + 日终失败报告；
  ④ 报告导出队列（T4.17）：pending 导出任务按创建序执行。

worker 主循环节拍 poll_seconds（默认 60s），到期即触发；单任务失败不阻塞其他任务。
SMTP 经环境变量注入（SMTP_HOST/SMTP_PORT/SMTP_FROM/SMTP_USER/SMTP_PASSWORD/SMTP_TLS），
未配置时邮件通道记 skipped 不阻塞。
"""
import argparse
import os
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerting import notifier
from app.alerting.engine import run_evaluation_round
from app.config import get_settings
from app.core.logging import configure_logging, get_logger, new_trace_id, set_trace_id
from app.db.session import get_session_factory
from app.models.alert import Alert, AlertRule
from app.models.user import User

logger = get_logger("worker.alerting")

# 周期评估间隔（详细设计 3.3：15 min，与快照刷新对齐）
EVAL_INTERVAL_SECONDS = 15 * 60
# 重试扫描回看窗口：仅扫描近 24h 内失败的 alert（过期不再重试）
RETRY_LOOKBACK = timedelta(hours=24)


def load_smtp_config() -> notifier.SmtpConfig | None:
    """从环境变量构造 SMTP 配置；SMTP_HOST 未配置返回 None（邮件通道 skipped）。"""
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return None
    return notifier.SmtpConfig(
        host=host,
        port=int(os.environ.get("SMTP_PORT", "25")),
        from_addr=os.environ.get("SMTP_FROM", "alert@agendascope.local"),
        username=os.environ.get("SMTP_USER") or None,
        password=os.environ.get("SMTP_PASSWORD") or None,
        use_tls=os.environ.get("SMTP_TLS", "").lower() in ("1", "true", "yes"),
        timeout_seconds=int(os.environ.get("SMTP_TIMEOUT_SECONDS", "10")),
    )


def _parse_retry_at(value) -> float | None:
    """notifier.next_retry_at 为 unix 时间戳字符串；解析失败返回 None。"""
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class AlertingWorker:
    """预警周期任务编排：规则评估 / 通知退避重试 / 订阅推送 / 报告导出队列。"""

    def __init__(
        self,
        session_factory=None,
        smtp_config: notifier.SmtpConfig | None = "auto",
        eval_interval_seconds: int = EVAL_INTERVAL_SECONDS,
        poll_seconds: int = 60,
    ):
        self.session_factory = session_factory or get_session_factory()
        self.smtp_config = load_smtp_config() if smtp_config == "auto" else smtp_config
        self.eval_interval_seconds = eval_interval_seconds
        self.poll_seconds = poll_seconds
        self._last_eval = 0.0

    # ------------------------------------------------------------------
    # 通知 hook（引擎评估时调用）
    # ------------------------------------------------------------------
    def _notify_hook(self, db: Session):
        def hook(alert: Alert, rule: AlertRule) -> dict:
            return notifier.notify_alert(db, alert, rule, smtp_config=self.smtp_config)
        return hook

    # ------------------------------------------------------------------
    # ① 周期评估（15 min）
    # ------------------------------------------------------------------
    def maybe_evaluate(self) -> bool:
        """到点触发一轮规则评估；返回本轮是否实际执行。"""
        if time.monotonic() - self._last_eval < self.eval_interval_seconds:
            return False
        db = self.session_factory()
        try:
            report = run_evaluation_round(db, notify_hook=self._notify_hook(db))
            db.commit()
            self._last_eval = time.monotonic()
            logger.info(
                "alert_eval_round_done",
                evaluated=report.evaluated_rules,
                triggered=report.triggered_count,
                suppressed=report.suppressed_count,
                storm_users=report.storm_users,
                errors=len(report.errors),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            self._last_eval = time.monotonic()  # 避免每轮空转重试
            logger.error("alert_eval_round_fail", error=str(exc)[:300])
            return False
        finally:
            db.close()

    # ------------------------------------------------------------------
    # ② 通知退避重试执行者
    # ------------------------------------------------------------------
    def _due_retry_channels(self, alert: Alert, now_ts: float) -> list[tuple[str, int]]:
        """提取 alert.notify_result 中到期的失败通道。

        返回 [(channel, retry_attempt)]；retry_attempt = 已尝试次数（0 起算的下一次序号）。
        """
        result = alert.notify_result or {}
        due: list[tuple[str, int]] = []
        for channel in ("email", "webhook"):
            ch = result.get(channel) or {}
            if ch.get("status") != "failed":
                continue
            attempts = ch.get("attempts") or []
            if len(attempts) > len(notifier.RETRY_BACKOFF_SECONDS):
                continue  # 首发 + 3 次重试（1m/5m/15m）均已用尽，转终态处理
            retry_at = _parse_retry_at(ch.get("next_retry_at"))
            if retry_at is not None and retry_at <= now_ts:
                due.append((channel, len(attempts)))
        return due

    def _finalize_failed_webhook(self, db: Session, alert: Alert, rule: AlertRule) -> None:
        """Webhook 重试上限仍失败：降级邮件 + 规则层面停用 webhook 通道（落库）+ 站内告警。"""
        title, body = notifier.render_alert_text(alert, rule)
        # 降级邮件：直接发往规则属主
        user = db.get(User, alert.user_id)
        if user and user.email and self.smtp_config is not None:
            ok_flag, err = notifier.send_email(
                self.smtp_config, user.email,
                f"[Webhook已失效降级] {title}", body,
            )
            logger.info(
                "alert_webhook_fallback_email",
                alert_id=str(alert.id), rule_id=str(rule.id),
                success=ok_flag, error=err,
            )
        else:
            logger.warning(
                "alert_webhook_fallback_email_skipped",
                alert_id=str(alert.id), rule_id=str(rule.id),
                reason="user_email_or_smtp_missing",
            )
        # 规则层面停用 webhook 通道（落库标记，不再往失效 URL 发送）
        channels = [c for c in (rule.notify_channels or []) if c != "webhook"]
        rule.notify_channels = channels or ["inapp"]
        # 站内告警：通知规则属主 webhook 已失效
        from app.alerting.engine import write_alert
        write_alert(db, rule, {
            "kind": "webhook_disabled",
            "message": (
                f"规则「{rule.name}」的 Webhook 通知连续 "
                f"{len(notifier.RETRY_BACKOFF_SECONDS)} 次重试仍失败，已自动降级为邮件/站内通知，"
                "并停用该规则的 webhook 通道。请检查 Webhook URL 后在规则中重新启用。"
            ),
            "failed_alert_id": str(alert.id),
            "webhook_url_host": (rule.webhook_url or "").split("/")[2] if rule.webhook_url else None,
        })
        logger.error(
            "alert_webhook_disabled",
            rule_id=str(rule.id), alert_id=str(alert.id),
        )

    def run_retry_scan(self, db: Session, now: datetime | None = None) -> dict:
        """扫描近 24h 内 notify_result 含失败通道且退避到期的 alert，执行重试。

        返回 {retried, succeeded, finalized} 统计。
        """
        now = now or datetime.now(UTC)
        now_ts = now.timestamp()
        stmt = (
            select(Alert)
            .where(Alert.triggered_at >= now - RETRY_LOOKBACK, Alert.notify_result.is_not(None))
            .order_by(Alert.triggered_at.desc())
            .limit(500)
        )
        stats = {"retried": 0, "succeeded": 0, "finalized": 0}
        for alert in db.scalars(stmt).all():
            result = alert.notify_result or {}
            rule = db.get(AlertRule, alert.rule_id)
            if rule is None:
                continue

            # 首发 + 3 次重试均失败的 webhook：终态处理（降级邮件 + 停用通道），仅执行一次
            wh = result.get("webhook") or {}
            if (
                wh.get("status") == "failed"
                and len(wh.get("attempts") or []) > len(notifier.RETRY_BACKOFF_SECONDS)
                and not wh.get("disabled")
            ):
                self._finalize_failed_webhook(db, alert, rule)
                wh["disabled"] = True
                result["webhook"] = wh
                alert.notify_result = dict(result)
                stats["finalized"] += 1
                db.flush()

            for channel, retry_attempt in self._due_retry_channels(alert, now_ts):
                try:
                    new_result = notifier.notify_alert(
                        db, alert, rule,
                        smtp_config=self.smtp_config, retry_attempt=retry_attempt,
                    )
                    stats["retried"] += 1
                    if (new_result.get(channel) or {}).get("status") == "ok":
                        stats["succeeded"] += 1
                except Exception as exc:  # noqa: BLE001 单条重试失败不阻塞其他
                    logger.error(
                        "alert_notify_retry_fail",
                        alert_id=str(alert.id), channel=channel, error=str(exc)[:200],
                    )
        db.flush()
        return stats

    def maybe_retry_notifications(self) -> bool:
        """每轮执行一次退避重试扫描；返回本轮是否有重试动作。"""
        db = self.session_factory()
        try:
            stats = self.run_retry_scan(db)
            db.commit()
            if stats["retried"] or stats["finalized"]:
                logger.info("alert_notify_retry_done", **stats)
            return bool(stats["retried"] or stats["finalized"])
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.error("alert_notify_retry_scan_fail", error=str(exc)[:300])
            return False
        finally:
            db.close()

    # ------------------------------------------------------------------
    # ③ 订阅推送 / ④ 报告导出队列（T4.16/T4.17，subscription/report 模块接入）
    # ------------------------------------------------------------------
    def maybe_send_subscriptions(self) -> bool:
        """到期的日报/周报订阅生成并投递 + 投递退避重试 + 日终失败报告。"""
        from app.alerting.subscription import run_subscription_round

        db = self.session_factory()
        try:
            stats = run_subscription_round(db, smtp_config=self.smtp_config)
            db.commit()
            if any(stats.values()):
                logger.info("subscription_round_done", **stats)
            return bool(any(stats.values()))
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.error("subscription_round_fail", error=str(exc)[:300])
            return False
        finally:
            db.close()

    def maybe_process_report_queue(self) -> bool:
        """执行 pending 报告导出任务（并发 >3 排队，按创建序）。"""
        from app.services.report_service import process_pending_exports

        db = self.session_factory()
        try:
            processed = process_pending_exports(db)
            db.commit()
            if processed:
                logger.info("report_export_queue_done", processed=processed)
            return processed > 0
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.error("report_export_queue_fail", error=str(exc)[:300])
            return False
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run_once(self) -> int:
        """单轮：各任务各试一次，返回本轮实际执行的任务数。"""
        set_trace_id(new_trace_id())
        done = 0
        if self.maybe_evaluate():
            done += 1
        if self.maybe_retry_notifications():
            done += 1
        if self.maybe_send_subscriptions():
            done += 1
        if self.maybe_process_report_queue():
            done += 1
        return done

    def run_forever(self) -> None:
        logger.info(
            "alerting_worker_start",
            eval_interval_seconds=self.eval_interval_seconds,
            poll_seconds=self.poll_seconds,
            smtp_configured=self.smtp_config is not None,
        )
        # 启动即首轮：评估 + 重试扫描 + 订阅 + 报告队列 都先跑一遍（不等到期）
        self.run_once()
        while True:
            time.sleep(self.poll_seconds)
            self.run_once()


def main() -> None:
    parser = argparse.ArgumentParser(description="AgendaScope 预警调度 worker（评估/重试/订阅/报告队列）")
    parser.add_argument("--once", action="store_true", help="单轮触发所有任务后退出（巡检/调试用）")
    parser.add_argument("--eval-once", action="store_true", help="仅触发一轮规则评估后退出")
    parser.add_argument("--retry-once", action="store_true", help="仅执行一轮通知退避重试扫描后退出")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    worker = AlertingWorker()
    if args.eval_once:
        worker._last_eval = 0.0
        worker.maybe_evaluate()
        return
    if args.retry_once:
        worker.maybe_retry_notifications()
        return
    if args.once:
        worker.run_once()
        return
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("alerting_worker_stop")


if __name__ == "__main__":
    main()
