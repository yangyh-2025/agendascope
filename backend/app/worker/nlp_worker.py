"""NLP worker 进程入口：python -m app.worker.nlp_worker（M2-1 管线接入）。

消费 raw:articles（采集中枢落库后 XADD，与 Phase 1 队列封装/消费者组风格一致）：
  语言识别 → 向量化落库 → ES 全文索引同步 → 延迟埋点 → ACK

可靠性语义：
- 先 XAUTOCLAIM 回收滞留 pending（如 ES 故障期未 ACK 的批），再读新消息
- 处理失败不 ACK，消息滞留 pending 待回收重投递；单消息尝试超 worker_max_attempts 进死信（raw:articles:dlq）
- ES 同步为有界重试（es_sync），耗尽后整批不 ACK 重投递——ES 恢复后自动追平，不死等
- 语言/向量在 ES 同步前已落 PG 提交，对外可见性不被 ES 阻塞（详细设计 3.1 可见性红线）

api_server 与 worker 共用代码库；本进程只跑消费循环，不启动 HTTP 服务。
"""
import argparse
import json
import os
import socket
import time
from uuid import UUID

from app.clustering import STREAM_EMBEDDED_ARTICLES
from app.config import get_settings
from app.core.logging import configure_logging, get_logger, new_trace_id, set_trace_id
from app.db.queue import STREAM_RAW_ARTICLES, StreamQueue
from app.db.redis_client import get_stream_redis
from app.db.session import get_session_factory
from app.nlp.config import get_nlp_settings
from app.nlp.embedding import Embedder
from app.nlp.es_sync import EsArticleIndexer
from app.nlp.language import LanguageDetector
from app.nlp.pipeline import NlpPipeline

logger = get_logger("worker.nlp")


class NlpWorker:
    def __init__(
        self,
        queue: StreamQueue | None = None,
        detector: LanguageDetector | None = None,
        embedder: Embedder | None = None,
        es_indexer: EsArticleIndexer | None = None,
    ):
        settings = get_nlp_settings()
        self.settings = settings
        self.stream = STREAM_RAW_ARTICLES
        self.group = settings.worker_group
        self.consumer = f"nlp-{socket.gethostname()}-{os.getpid()}"
        self.queue = queue or StreamQueue(get_stream_redis())
        self.session_factory = get_session_factory()
        self.detector = detector or LanguageDetector()
        self.embedder = embedder or Embedder()
        # ES 同步可禁用（低内存部署 NLP_ES_SYNC_ENABLED=false 时跳过 ES，搜索走 PG 降级；
        # 语言+向量先落 PG 已保证可见性，ES 故障不阻塞 pipeline）
        if es_indexer is not None:
            self.es_indexer = es_indexer
        elif getattr(settings, "es_sync_enabled", True):
            self.es_indexer = EsArticleIndexer()
        else:
            self.es_indexer = None
        self._attempts: dict[str, int] = {}  # 消息级尝试计数（进程内；重启后由 DLQ 兜底语义不变）

    def _reclaim_pending(self) -> list:
        """回收滞留超 worker_reclaim_idle_ms 的 pending 消息（重投递入口）。"""
        try:
            _, messages, _ = self.queue.client.xautoclaim(
                self.stream, self.group, self.consumer,
                min_idle_time=self.settings.worker_reclaim_idle_ms,
                start_id="0-0", count=self.settings.worker_batch_size,
            )
            return [(str(mid), fields) for mid, fields in messages]
        except Exception as exc:  # noqa: BLE001 回收失败不阻塞新消息消费
            logger.warning("xautoclaim_fail", error=str(exc))
            return []

    def _handle_failure(self, entries: list, exc: Exception) -> None:
        """批处理失败：计数尝试次数，超限进死信并 ACK，其余滞留 pending 待回收重投递。"""
        logger.error("nlp_batch_fail", batch=len(entries), error=str(exc)[:300])
        for msg_id, fields in entries:
            attempts = self._attempts.get(msg_id, 0) + 1
            self._attempts[msg_id] = attempts
            if attempts >= self.settings.worker_max_attempts:
                self.queue.to_dlq(self.stream, msg_id, dict(fields), reason=f"attempts_exceeded: {exc}"[:500])
                self.queue.ack(self.stream, self.group, msg_id)
                self._attempts.pop(msg_id, None)
                logger.error("nlp_msg_to_dlq", msg_id=msg_id, attempts=attempts)

    def process_entries(self, entries: list) -> int:
        """处理一批消息，返回成功处理的消息数；失败批按 _handle_failure 处置。"""
        article_ids: list[UUID] = []
        valid: list = []
        for msg_id, fields in entries:
            try:
                data = json.loads(fields.get("data", "{}"))
                article_ids.append(UUID(data["article_id"]))
                valid.append((msg_id, fields))
            except (ValueError, KeyError, TypeError) as exc:
                self.queue.to_dlq(self.stream, msg_id, dict(fields), reason=f"bad_payload: {exc}")
                self.queue.ack(self.stream, self.group, msg_id)
                logger.error("nlp_bad_payload", msg_id=msg_id, error=str(exc))
        if not valid:
            return 0
        set_trace_id(valid[0][1].get("trace_id") or new_trace_id())
        db = self.session_factory()
        try:
            pipeline = NlpPipeline(db, self.detector, self.embedder, self.es_indexer)
            pipeline.process(article_ids)
            # 向量化已落库：投递 nlp:embedded 供聚类 worker 在线归簇（聚类接在向量化之后）。
            # 投递失败按批失败处理不 ACK，重投递后聚类侧幂等去重，不重复建簇。
            trace_id = valid[0][1].get("trace_id") or ""
            for (_, fields), article_id in zip(valid, article_ids, strict=True):
                self.queue.publish(
                    STREAM_EMBEDDED_ARTICLES,
                    {"article_id": str(article_id)},
                    trace_id=fields.get("trace_id") or trace_id,
                )
        except Exception as exc:  # noqa: BLE001 统一失败处置：重投递/死信
            db.rollback()
            self._handle_failure(valid, exc)
            return 0
        finally:
            db.close()
        for msg_id, _ in valid:
            self.queue.ack(self.stream, self.group, msg_id)
            self._attempts.pop(msg_id, None)
        return len(valid)

    def run_once(self) -> int:
        """单轮：先回收滞留 pending，再读新消息。返回处理消息数（测试与巡检用）。

        整体异常保护：Redis 瞬时超时（低内存/swap 下）不应崩进程——记日志下轮重试，
        消息滞留 pending 由回收/重投递兜底（可靠性语义不变）。
        """
        processed = 0
        try:
            reclaimed = self._reclaim_pending()
            if reclaimed:
                processed += self.process_entries(reclaimed)
            entries = self.queue.consume(
                self.stream, self.group, self.consumer,
                count=self.settings.worker_batch_size, block_ms=self.settings.worker_block_ms,
            )
            entries = [(str(mid), fields) for mid, fields in entries]
            if entries:
                processed += self.process_entries(entries)
        except Exception as exc:  # noqa: BLE001 单轮整体失败不崩进程（Redis 超时/DB 抖动）
            logger.error("nlp_round_fail", error=str(exc)[:300])
        return processed

    def run_forever(self) -> None:
        logger.info(
            "nlp_worker_start",
            stream=self.stream, group=self.group, consumer=self.consumer,
            batch=self.settings.worker_batch_size, es_index=self.es_indexer.index,
        )
        while True:
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 兜底：任何未捕获异常都不崩进程
                logger.error("nlp_worker_loop_fail", error=str(exc)[:300])
                time.sleep(2.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgendaScope NLP worker")
    parser.add_argument("--once", action="store_true", help="单轮处理后退出（巡检/调试用）")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    worker = NlpWorker()
    if args.once:
        worker.run_once()
        return
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("nlp_worker_stop")


if __name__ == "__main__":
    main()
