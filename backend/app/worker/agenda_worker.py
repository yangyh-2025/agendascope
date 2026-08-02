"""议程引擎 worker 进程入口：python -m app.worker.agenda_worker（M3-1 收尾接入）。

按各自周期到期触发三类周期任务，均直接写库：
  ① 次日自动归并（T3.3，默认 60min）：candidate nascent 微簇 vs 历史活跃议题
     跨语言向量比对 ≥0.85 并入旧议题，topic_id 复用 + revision_log 留痕；
  ② 议题消亡扫描（T3.2，默认 60min）：连续 7 天无新报道自动归档，human_locked_fields
     非空议题不自动消亡（尊重人工结论）；
  ③ 动态高频实体黑名单刷新（T3.5，默认 24h）：近 30 天 Top-50 实体写 Redis Set
     `entity:blacklist` TTL 48h；Redis 失败保旧值不抛错（优化非正确性依赖）。

worker 主循环节拍 worker_poll_seconds（默认 60s），到期即触发对应任务，未到期不空转；
单任务失败不阻塞其他任务（独立 try/except 记日志后下轮再试）；三类任务各自独立
db session 与 commit/rollback（互不污染）。
"""
import argparse
import time

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.entity_blacklist import refresh_entity_blacklist
from app.agenda_engine.lifecycle import sweep_archived
from app.agenda_engine.merge import nextday_merge
from app.config import get_settings
from app.core.logging import configure_logging, get_logger, new_trace_id, set_trace_id
from app.db.redis_client import get_cache_redis
from app.db.session import get_session_factory

logger = get_logger("worker.agenda")


class AgendaWorker:
    """议程引擎周期任务编排：归并 / 消亡 / 实体黑名单。"""

    def __init__(self, session_factory=None, redis_client=None, llm_annotator=None):
        self.settings = get_agenda_settings()
        self.session_factory = session_factory or get_session_factory()
        self.redis = redis_client if redis_client is not None else get_cache_redis()
        self._llm_annotator = llm_annotator  # 默认 None=纯向量归并（回放验证最优）；
                                            # 注入 TopicAnnotator 才启用 LLM merge 确认（opt-in）
        self._last_merge = 0.0
        self._last_sweep = 0.0
        self._last_blacklist = 0.0

    # ------------------------------------------------------------------
    # 周期任务（独立 db session + commit/rollback；失败不阻塞其他任务）
    # ------------------------------------------------------------------
    def maybe_merge(self) -> bool:
        """到点触发次日归并；返回本轮是否实际执行。"""
        interval_s = self.settings.merge_interval_minutes * 60
        # _last_merge=0.0 是"从未执行"哨兵：机器 uptime 短于周期时 monotonic() 仍小于
        # interval，若直接比较会误判未到期，故哨兵必须先判（启动即首轮触发）
        if self._last_merge > 0.0 and time.monotonic() - self._last_merge < interval_s:
            return False
        db = self.session_factory()
        try:
            report = nextday_merge(db, redis_client=self.redis, llm_annotator=self._llm_annotator)
            db.commit()
            self._last_merge = time.monotonic()
            logger.info(
                "agenda_merge_done",
                merged=len(report.merged),
                new_topics=len(report.new_topics),
                skipped_no_merge=len(report.skipped_no_merge),
                skipped_locked=len(report.skipped_locked),
                skipped_llm=len(report.skipped_llm),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            self._last_merge = time.monotonic()  # 避免每轮空转重试
            logger.error("agenda_merge_fail", error=str(exc)[:300])
            return False
        finally:
            db.close()

    def maybe_sweep(self) -> bool:
        """到点触发消亡扫描；返回本轮是否实际执行。"""
        interval_s = self.settings.sweep_interval_minutes * 60
        if self._last_sweep > 0.0 and time.monotonic() - self._last_sweep < interval_s:
            return False
        db = self.session_factory()
        try:
            archived = sweep_archived(db)
            db.commit()
            self._last_sweep = time.monotonic()
            if archived:
                logger.info("agenda_sweep_done", archived=len(archived))
            return True
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            self._last_sweep = time.monotonic()
            logger.error("agenda_sweep_fail", error=str(exc)[:300])
            return False
        finally:
            db.close()

    def maybe_refresh_blacklist(self) -> bool:
        """到点刷新实体黑名单；返回本轮是否实际执行。"""
        interval_s = self.settings.entity_blacklist_refresh_hours * 3600
        if self._last_blacklist > 0.0 and time.monotonic() - self._last_blacklist < interval_s:
            return False
        db = self.session_factory()
        try:
            blacklist = refresh_entity_blacklist(db, self.redis)
            self._last_blacklist = time.monotonic()
            logger.info("agenda_blacklist_done", size=len(blacklist))
            return True
        except Exception as exc:  # noqa: BLE001
            self._last_blacklist = time.monotonic()
            logger.error("agenda_blacklist_fail", error=str(exc)[:300])
            return False
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run_once(self) -> int:
        """单轮：三类任务各试一次，返回本轮实际执行的任务数。"""
        set_trace_id(new_trace_id())
        done = 0
        if self.maybe_merge():
            done += 1
        if self.maybe_sweep():
            done += 1
        if self.maybe_refresh_blacklist():
            done += 1
        return done

    def run_forever(self) -> None:
        logger.info(
            "agenda_worker_start",
            merge_interval_minutes=self.settings.merge_interval_minutes,
            sweep_interval_minutes=self.settings.sweep_interval_minutes,
            blacklist_refresh_hours=self.settings.entity_blacklist_refresh_hours,
            poll_seconds=self.settings.worker_poll_seconds,
        )
        # 启动即首轮全触发：归并/消亡/黑名单 都先跑一遍（不等到期）
        self.run_once()
        while True:
            time.sleep(self.settings.worker_poll_seconds)
            self.run_once()


def main() -> None:
    parser = argparse.ArgumentParser(description="AgendaScope 议程引擎 worker（归并/消亡/实体黑名单）")
    parser.add_argument("--once", action="store_true", help="单轮触发所有到期任务后退出（巡检/调试用）")
    parser.add_argument("--merge-once", action="store_true", help="仅触发一轮次日归并后退出")
    parser.add_argument("--sweep-once", action="store_true", help="仅触发一轮消亡扫描后退出")
    parser.add_argument("--blacklist-once", action="store_true", help="仅刷新一轮实体黑名单后退出")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    worker = AgendaWorker()
    if args.merge_once:
        worker._last_merge = 0.0
        worker.maybe_merge()
        return
    if args.sweep_once:
        worker._last_sweep = 0.0
        worker.maybe_sweep()
        return
    if args.blacklist_once:
        worker._last_blacklist = 0.0
        worker.maybe_refresh_blacklist()
        return
    if args.once:
        worker.run_once()
        return
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("agenda_worker_stop")


if __name__ == "__main__":
    main()
