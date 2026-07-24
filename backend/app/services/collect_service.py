"""采集中枢接入服务：校验 → uuid/url_hash 幂等去重 → 落库 → XADD raw:articles → 回写任务计数。

可见性优先：文章写入即设 visible_at（published_at→visible_at 为延迟红线考核点），
NLP/聚类在 Phase 2 经 raw:articles 异步补标签，不阻塞可见。
"""
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.collector.governance import Governance
from app.collector.utils import url_hash
from app.core.errors import CODE_PARAM_INVALID, BizError
from app.core.logging import get_logger, get_trace_id
from app.db.queue import STREAM_RAW_ARTICLES, StreamQueue
from app.models.article import Article
from app.models.collection import CollectionJob
from app.models.source import Source
from app.schemas.collect import CollectedPayload

logger = get_logger("collect_service")

_UUID_DEDUP_TTL = 72 * 3600


class CollectService:
    def __init__(self, db: Session, redis_client=None, queue: StreamQueue | None = None):
        self.db = db
        self.redis = redis_client
        self.queue = queue
        self.gov = Governance(db, redis_client)

    def ingest(self, payload: CollectedPayload) -> dict:
        source = self.db.get(Source, payload.source_id)
        if source is None:
            raise BizError(CODE_PARAM_INVALID, "source_id 不存在")

        uuid_key = f"dedup:uuid:{payload.uuid}"
        if self.redis is not None and self.redis.exists(uuid_key):
            return {"uuid": str(payload.uuid), "accepted": True, "duplicate": True}

        hash_hex = url_hash(payload.url)
        if self.gov.is_duplicate(hash_hex):  # 防重①：持久去重（Redis 指纹 + DB 唯一约束）
            if self.redis is not None:
                self.redis.setex(uuid_key, _UUID_DEDUP_TTL, "1")
            return {"uuid": str(payload.uuid), "accepted": True, "duplicate": True}

        now = datetime.now(UTC)
        published_at = payload.pub_time or now
        time_source = payload.time_source or ("feed" if payload.pub_time else "crawled")
        article = Article(
            source_id=source.id,
            url=payload.url,
            url_hash=hash_hex,
            title=payload.title,
            content=payload.content,
            summary=payload.content[:500],
            language=source.language,
            published_at=published_at,
            time_source=time_source,
            visible_at=now,
            content_status=payload.content_status,
            source_channel=source.collect_mode,
            country_code=source.country_code,
        )
        self.db.add(article)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            return {"uuid": str(payload.uuid), "accepted": True, "duplicate": True}

        self.gov.record_fingerprint(hash_hex)
        if self.redis is not None:
            self.redis.setex(uuid_key, _UUID_DEDUP_TTL, "1")

        if payload.job_id:
            job = self.db.get(CollectionJob, payload.job_id)
            if job is not None:
                job.articles_new = (job.articles_new or 0) + 1
                self.db.flush()

        if self.queue is not None:
            try:
                self.queue.publish(
                    STREAM_RAW_ARTICLES,
                    {"article_id": str(article.id), "uuid": str(payload.uuid), "source_id": str(source.id)},
                    trace_id=get_trace_id(),
                )
            except Exception as exc:  # noqa: BLE001 队列故障不阻塞入库可见
                logger.warning("stream_publish_fail", article_id=str(article.id), error=str(exc))

        self.db.commit()
        logger.info(
            "article_ingested", article_id=str(article.id), source_id=str(source.id),
            url_hash=hash_hex, content_status=payload.content_status,
        )
        return {"uuid": str(payload.uuid), "accepted": True, "duplicate": False}
