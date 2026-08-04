"""监控对象关系抽取 worker 进程入口：python -m app.worker.relation_worker

职责：每日跑一次（默认凌晨 03:00 错峰），扫描过去 24h 的文章，
对其含种子实体的文章调用 LLM 抽取实体间关系，落 entity_relations + relation_evidences，
并对所有 active 关系做时间衰减。

降级：
- RELATION_EXTRACT_ENABLED=false 时整体停用
- LLM 调用失败 / evidence_quote 校验失败 时静默跳过该条
- 单篇文章失败不阻塞整体跑批

资源预算：单容器 256MB；每日一次跑完即 sleep 到下一天。
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.core.logging import configure_logging, get_logger, new_trace_id, set_trace_id
from app.db.session import get_session_factory
from app.llm.annotator import TopicAnnotator
from app.services.relation_extraction import run_relation_extraction_round

logger = get_logger("worker.relation")

# 默认凌晨 03:00（服务器本地时区）起跑
DEFAULT_RUN_HOUR = int(os.environ.get("RELATION_RUN_HOUR", "3"))
# 单轮处理文章上限（防爆内存/超时）
DEFAULT_BATCH_LIMIT = int(os.environ.get("RELATION_BATCH_LIMIT", "300"))
# 扫描回看窗口（小时）
DEFAULT_LOOKBACK_HOURS = int(os.environ.get("RELATION_LOOKBACK_HOURS", "24"))
# 心跳间隔（秒）—— 检查是否到达下一次跑批时刻
POLL_SECONDS = 60


def _seconds_until_next_run(now: datetime, run_hour: int) -> float:
    """计算到下一次 run_hour 的秒数（按服务器本地时区）。"""
    next_run = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


class RelationWorker:
    def __init__(
        self,
        session_factory=None,
        annotator: TopicAnnotator | None = None,
        run_hour: int = DEFAULT_RUN_HOUR,
        lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        run_on_start: bool = False,
    ):
        self.session_factory = session_factory or get_session_factory()
        self._annotator = annotator
        self.run_hour = run_hour
        self.lookback_hours = lookback_hours
        self.batch_limit = batch_limit
        self.run_on_start = run_on_start

    @property
    def annotator(self) -> TopicAnnotator:
        if self._annotator is None:
            self._annotator = TopicAnnotator()
        return self._annotator

    def run_once(self) -> dict:
        """执行一轮关系抽取跑批。"""
        set_trace_id(new_trace_id())
        db = self.session_factory()
        try:
            stats = run_relation_extraction_round(
                db,
                annotator=self.annotator,
                hours=self.lookback_hours,
                limit=self.batch_limit,
            )
            logger.info("relation_round_done", extra=stats)
            return stats
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("relation_round_fail", extra={"error": str(exc)[:300]})
            return {"error": str(exc)[:200]}
        finally:
            db.close()

    def run_forever(self) -> None:
        logger.info(
            "relation_worker_start",
            extra={
                "run_hour": self.run_hour,
                "lookback_hours": self.lookback_hours,
                "batch_limit": self.batch_limit,
            },
        )
        # 启动即跑（可选）：用于部署后首轮补抓
        if self.run_on_start:
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                logger.exception("relation_initial_run_fail")
        while True:
            now = datetime.now(UTC).astimezone()  # 服务器本地时区
            wait = _seconds_until_next_run(now, self.run_hour)
            logger.info("relation_next_run", extra={"wait_seconds": int(wait)})
            # 睡到下次跑批，但每分钟醒来一次便于信号处理
            slept = 0.0
            while slept < wait:
                time.sleep(min(POLL_SECONDS, wait - slept))
                slept += POLL_SECONDS
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                logger.exception("relation_run_fail")


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="只跑一轮就退出（调试用）")
    parser.add_argument("--run-on-start", action="store_true", help="启动立即跑一轮")
    parser.add_argument("--run-hour", type=int, default=DEFAULT_RUN_HOUR)
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--batch-limit", type=int, default=DEFAULT_BATCH_LIMIT)
    args = parser.parse_args()

    if os.environ.get("RELATION_EXTRACT_ENABLED", "true").lower() in ("false", "0", "no"):
        logger.info("relation_worker_disabled_by_env")
        # 即使禁用也保持容器存活，避免 compose 重启循环
        while True:
            time.sleep(3600)

    worker = RelationWorker(
        run_hour=args.run_hour,
        lookback_hours=args.lookback_hours,
        batch_limit=args.batch_limit,
        run_on_start=args.run_on_start,
    )
    if args.once:
        worker.run_once()
        return
    worker.run_forever()


if __name__ == "__main__":
    main()
