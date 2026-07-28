"""事件检测 worker 进程入口：python -m app.worker.detection_worker（M3-2/M3-3 主链路接入）。

周期对活跃议题跑完整事件检测链路（app.agenda_engine.detection.run_detection_cycle）：
回声消除折叠 → 实体登记（NER 入 persons_orgs）→ 首发锚点判定 → LLM 首发表述判定
（media_time_fallback 回落）→ 跟随国序列 → 统计佐证 → 事件判定 → LLM 终审。

与 agenda_worker 同风格：主循环节拍 worker_poll_seconds，按 detection_interval_minutes
到期触发；run_detection_cycle 内部按议题独立 commit/rollback（单议题失败不阻塞整轮）；
worker 层仅兜底整轮级异常（如 DB 连接断开），记 error 后下轮再试。

LLM 通过 TopicAnnotator 依赖注入：构造时惰性（模型首次推理时才 load），
monitor.degraded 时整轮走 media_time_fallback 并写 P1 降级告警（详见 detection.py）。
"""
import argparse
import time

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.detection import run_detection_cycle
from app.config import get_settings
from app.core.logging import configure_logging, get_logger, new_trace_id, set_trace_id
from app.db.redis_client import get_cache_redis
from app.db.session import get_session_factory

logger = get_logger("worker.detection")


class DetectionWorker:
    """事件检测周期任务编排：对活跃议题跑 detect_origin 全链路。"""

    def __init__(self, session_factory=None, redis_client=None, llm_annotator=None):
        self.settings = get_agenda_settings()
        self.session_factory = session_factory or get_session_factory()
        self.redis = redis_client if redis_client is not None else get_cache_redis()
        self._llm_annotator = llm_annotator  # None 时首次执行才构造（惰性加载模型）
        self._last_detect = 0.0

    def _get_annotator(self):
        """惰性构造 TopicAnnotator（LLMEngine 首次推理才 load 模型，不占启动时间）。"""
        if self._llm_annotator is None:
            from app.llm.annotator import TopicAnnotator

            self._llm_annotator = TopicAnnotator()
        return self._llm_annotator

    def maybe_detect(self) -> bool:
        """到点触发一轮事件检测；返回本轮是否实际执行。"""
        interval_s = self.settings.detection_interval_minutes * 60
        if time.monotonic() - self._last_detect < interval_s:
            return False
        db = self.session_factory()
        try:
            report = run_detection_cycle(
                db,
                llm_annotator=self._get_annotator(),
                redis_client=self.redis,
            )
            self._last_detect = time.monotonic()
            logger.info(
                "detection_worker_done",
                scanned=report.scanned,
                events=report.events_created,
                reviewed=report.events_reviewed,
                fallback_topics=report.fallback_topics,
                failed=len(report.failed_topics),
            )
            return True
        except Exception as exc:  # noqa: BLE001 整轮级异常（如 DB 断开）：记 error 下轮再试
            db.rollback()
            self._last_detect = time.monotonic()  # 避免每轮空转重试
            logger.error("detection_worker_fail", error=str(exc)[:300])
            return False
        finally:
            db.close()

    def run_once(self) -> int:
        """单轮：到期则执行一次检测，返回本轮实际执行的任务数。"""
        set_trace_id(new_trace_id())
        return 1 if self.maybe_detect() else 0

    def run_forever(self) -> None:
        logger.info(
            "detection_worker_start",
            detection_interval_minutes=self.settings.detection_interval_minutes,
            topic_batch_size=self.settings.detection_topic_batch_size,
            poll_seconds=self.settings.worker_poll_seconds,
        )
        # 启动即首轮触发（不等到期）
        self._last_detect = 0.0
        self.run_once()
        while True:
            time.sleep(self.settings.worker_poll_seconds)
            self.run_once()


def main() -> None:
    parser = argparse.ArgumentParser(description="AgendaScope 事件检测 worker（首发源/跟随序列/事件判定/终审）")
    parser.add_argument("--once", action="store_true", help="单轮触发检测后退出（巡检/调试用）")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    worker = DetectionWorker()
    if args.once:
        worker.run_once()
        return
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("detection_worker_stop")


if __name__ == "__main__":
    main()
