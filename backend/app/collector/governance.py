"""采集治理状态机与防重三层（T1.15，复刻 IIS 治理设计）。

六态流转：PENDING → RUNNING → SUCCESS / TEMP_FAIL → (退避重试) PENDING / PERM_FAIL
                     └→ SKIPPED（should_crawl 裁决去重跳过）

防重三层（对齐 IIS）：
  ① 持久去重：articles.url_hash 唯一约束 + Redis 指纹 dedup:url:{hash}（72h）
  ② 提交失败内存缓存下轮重发（Submitter 内实现，见 submitter.py）
  ③ 任务内 URL 过滤：单轮任务内已见 URL 集合

源健康状态机（T1.22）：连续 3 次失败 → degraded；degraded 超 24h → failed；连续 2 次成功 → active。
源失败率超阈值（默认 10%，24h 滑动）→ 写 alerts 表主动告警（US-03）。
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models.collection import (
    JOB_PENDING,
    JOB_PERM_FAIL,
    JOB_RUNNING,
    JOB_SKIPPED,
    JOB_SUCCESS,
    JOB_TEMP_FAIL,
    CollectionJob,
)
from app.models.source import Source

logger = get_logger("governance")

# 退避重试基数（秒）：第 n 次重试等待 BASE * 2^(n-1)
RETRY_BACKOFF_BASE_SECONDS = 300
SOURCE_FAIL_TO_DEGRADED = 3
SOURCE_SUCCESS_TO_ACTIVE = 2   # degraded 连续 2 次成功才恢复（T1.22）
DEGRADED_TO_FAILED_HOURS = 24
DEDUP_FINGERPRINT_TTL_SECONDS = 72 * 3600
_SUCCESS_STREAK_KEY = "source:health:success_streak:{source_id}"


@dataclass
class FailureDecision:
    status: str           # TEMP_FAIL / PERM_FAIL
    retry_count: int
    next_run_at: datetime | None


def decide_failure(retry_count: int, max_retries: int, now: datetime) -> FailureDecision:
    """失败裁决（纯函数）：retry_count+1 后未超上限 → TEMP_FAIL 退避重试；超限 → PERM_FAIL 不再调度。"""
    new_count = retry_count + 1
    if new_count > max_retries:
        return FailureDecision(status=JOB_PERM_FAIL, retry_count=new_count, next_run_at=None)
    backoff = RETRY_BACKOFF_BASE_SECONDS * (2 ** (new_count - 1))
    return FailureDecision(status=JOB_TEMP_FAIL, retry_count=new_count, next_run_at=now + timedelta(seconds=backoff))


def should_crawl(status: str, next_run_at: datetime | None, now: datetime) -> bool:
    """统一裁决点：仅 PENDING/TEMP_FAIL 且到期的任务可进入调度；终态（SUCCESS/PERM_FAIL/SKIPPED）不再调度。"""
    if status not in (JOB_PENDING, JOB_TEMP_FAIL):
        return False
    return next_run_at is None or next_run_at <= now


class Governance:
    """采集治理：collection_jobs 六态流转 + 防重 + 源健康状态机 + 失败率告警。"""

    def __init__(self, db: Session, redis_client=None):
        self.db = db
        self.redis = redis_client
        self.settings = get_settings()

    # ---------- 任务生命周期 ----------

    def create_job(self, source_id, channel: str, scheduled_at: datetime) -> CollectionJob:
        job = CollectionJob(source_id=source_id, channel=channel, scheduled_at=scheduled_at, status=JOB_PENDING)
        self.db.add(job)
        self.db.flush()
        return job

    def job_should_run(self, job: CollectionJob, now: datetime | None = None) -> bool:
        return should_crawl(job.status, job.next_run_at, now or datetime.now(timezone.utc))

    def mark_running(self, job: CollectionJob) -> None:
        job.status = JOB_RUNNING
        job.started_at = datetime.now(timezone.utc)
        self.db.flush()

    def mark_success(self, job: CollectionJob, articles_found: int, articles_new: int,
                     latency_stats: dict | None = None, http_status: int | None = None) -> None:
        job.status = JOB_SUCCESS
        job.finished_at = datetime.now(timezone.utc)
        job.articles_found = articles_found
        job.articles_new = articles_new
        job.latency_stats = latency_stats
        if http_status is not None:
            job.http_status = http_status
        self.db.flush()

    def mark_failure(self, job: CollectionJob, error: str, http_status: int | None = None) -> FailureDecision:
        decision = decide_failure(job.retry_count, self.settings.crawl_max_retries, datetime.now(timezone.utc))
        job.status = decision.status
        job.retry_count = decision.retry_count
        job.next_run_at = decision.next_run_at
        job.finished_at = datetime.now(timezone.utc)
        job.error = error[:2000]
        if http_status is not None:
            job.http_status = http_status
        self.db.flush()
        logger.warning(
            "job_failure", job_id=str(job.id), source_id=str(job.source_id),
            status=decision.status, retry_count=decision.retry_count, error=error[:200],
        )
        return decision

    def mark_skipped(self, job: CollectionJob, reason: str) -> None:
        job.status = JOB_SKIPPED
        job.finished_at = datetime.now(timezone.utc)
        job.error = reason[:500]
        self.db.flush()

    # ---------- 防重①：持久去重（Redis 指纹 + DB 唯一约束兜底） ----------

    def dedup_key(self, hash_hex: str) -> str:
        return f"dedup:url:{hash_hex}"

    def is_duplicate(self, hash_hex: str) -> bool:
        from app.models.article import Article

        if self.redis is not None and self.redis.exists(self.dedup_key(hash_hex)):
            return True
        return self.db.scalar(select(Article.id).where(Article.url_hash == hash_hex).limit(1)) is not None

    def record_fingerprint(self, hash_hex: str) -> None:
        if self.redis is not None:
            self.redis.setex(self.dedup_key(hash_hex), DEDUP_FINGERPRINT_TTL_SECONDS, "1")

    # ---------- 源健康状态机（T1.22） ----------

    def update_source_health(self, source: Source, success: bool, reason: str = "") -> str | None:
        """按本轮采集结果推进源健康状态机；返回新状态（未变化返回 None）。状态变更留 status_history。

        恢复语义（T1.22）：degraded 须连续 2 次成功才恢复 active，连胜计数存 Redis；
        任何失败清零连胜。Redis 不可用时退化为单次成功即恢复（不阻塞主链路）。
        """
        old_status = source.status
        now = datetime.now(timezone.utc)
        if success:
            source.consecutive_failures = 0
            source.last_success_at = now
            if source.status == "degraded":
                if self.redis is None:
                    source.status = "active"
                    source.degraded_since = None
                else:
                    streak = self.redis.incr(_SUCCESS_STREAK_KEY.format(source_id=source.id))
                    if streak >= SOURCE_SUCCESS_TO_ACTIVE:
                        source.status = "active"
                        source.degraded_since = None
                        self.redis.delete(_SUCCESS_STREAK_KEY.format(source_id=source.id))
        else:
            source.consecutive_failures = (source.consecutive_failures or 0) + 1
            if self.redis is not None:
                self.redis.delete(_SUCCESS_STREAK_KEY.format(source_id=source.id))
            if source.status == "active" and source.consecutive_failures >= SOURCE_FAIL_TO_DEGRADED:
                source.status = "degraded"
                source.degraded_since = now
            elif (
                source.status == "degraded"
                and source.degraded_since
                and now - source.degraded_since > timedelta(hours=DEGRADED_TO_FAILED_HOURS)
            ):
                source.status = "failed"

        if source.status != old_status:
            history = list(source.status_history or [])
            history.append({
                "from": old_status,
                "to": source.status,
                "at": now.isoformat(),
                "reason": reason or ("采集成功恢复" if success else "采集失败"),
                "actor": "system",
            })
            source.status_history = history[-20:]  # 留最近 20 条（US-03 AC4）
            logger.warning(
                "source_status_change", source_id=str(source.id), old=old_status, new=source.status, reason=reason,
            )
            self.db.flush()
            return source.status
        self.db.flush()
        return None

    # ---------- 源失败率主动告警（US-03，IIS 弱项自建补齐） ----------

    def source_fail_rate(self, source_id, window_hours: int | None = None) -> float:
        hours = window_hours or self.settings.source_fail_rate_window_hours
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        total, failed = self.db.execute(
            select(
                func.count(),
                func.count().filter(CollectionJob.status.in_((JOB_TEMP_FAIL, JOB_PERM_FAIL))),
            ).where(CollectionJob.source_id == source_id, CollectionJob.scheduled_at >= since)
        ).one()
        if not total:
            return 0.0
        return failed / total

    def maybe_alert_source_fail_rate(self, source: Source) -> bool:
        """源失败率超阈值 → 写 alerts 表（防抖：同源 1h 内不重复告警）。返回是否触发告警。"""
        rate = self.source_fail_rate(source.id)
        if rate <= self.settings.source_fail_rate_alert_threshold:
            return False
        if self.redis is not None:
            debounce_key = f"alert:source_fail:{source.id}"
            if self.redis.exists(debounce_key):
                return False
            self.redis.setex(debounce_key, 3600, "1")

        from app.models.alert import Alert
        from app.services.seed_service import ensure_admin, ensure_system_rules

        admin = ensure_admin(self.db)
        rule = ensure_system_rules(self.db, admin)
        self.db.add(Alert(
            rule_id=rule.id,
            user_id=admin.id,
            payload={
                "kind": "source_fail_rate",
                "source_id": str(source.id),
                "source_name": source.name,
                "country_code": source.country_code,
                "fail_rate": round(rate, 4),
                "threshold": self.settings.source_fail_rate_alert_threshold,
                "window_hours": self.settings.source_fail_rate_window_hours,
            },
        ))
        self.db.flush()
        logger.warning(
            "source_fail_rate_alert", source_id=str(source.id), rate=round(rate, 4),
            threshold=self.settings.source_fail_rate_alert_threshold,
        )
        return True


class TaskUrlFilter:
    """防重③：任务内 URL 过滤（单轮采集内已见 URL 不再重复提交）。"""

    def __init__(self):
        self._seen: set[str] = set()

    def seen(self, hash_hex: str) -> bool:
        return hash_hex in self._seen

    def add(self, hash_hex: str) -> None:
        self._seen.add(hash_hex)

    def filter_new(self, hashes: list[str]) -> list[str]:
        return [h for h in hashes if h not in self._seen]
