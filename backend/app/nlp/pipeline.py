"""NLP 基础管线编排（M2-1）：语言识别 → 向量化落库 → ES 同步 → 延迟埋点。

输入为一批 article_id（来自 raw:articles 消费），PG 为唯一事实源：
语言/置信度/embedding 先落 PG 并提交（对外可见性不被 ES 阻塞），
ES 同步失败有界重试后整批重投递，延迟埋点 ON CONFLICT 幂等。

性能口径（T2.2 目标：向量化+落库单篇 P95 ≤5s，CPU）：耗时实测值记录于 CHANGELOG Phase 2。
"""
import time
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.article import Article
from app.nlp.embedding import Embedder, build_embedding_text
from app.nlp.es_sync import ArticleDoc, EsArticleIndexer
from app.nlp.language import LanguageDetector
from app.nlp.latency import record_sample

logger = get_logger("nlp.pipeline")


@dataclass
class BatchMetrics:
    processed: int = 0
    low_confidence: int = 0
    language_ms: float = 0.0
    embed_ms: float = 0.0
    db_ms: float = 0.0
    es_ms: float = 0.0
    per_article_ms: list[float] = field(default_factory=list)  # 向量化+落库单篇耗时（性能目标度量）


class NlpPipeline:
    def __init__(
        self,
        db: Session,
        detector: LanguageDetector,
        embedder: Embedder,
        es_indexer: EsArticleIndexer | None = None,
    ):
        self.db = db
        self.detector = detector
        self.embedder = embedder
        self.es_indexer = es_indexer

    def process(self, article_ids: list[UUID]) -> BatchMetrics:
        metrics = BatchMetrics()
        if not article_ids:
            return metrics
        articles = self.db.scalars(select(Article).where(Article.id.in_(article_ids))).all()
        if not articles:
            return metrics

        # ① 语言识别（低置信回落源默认语言，置信度值留痕）
        t0 = time.perf_counter()
        for article in articles:
            source_default = article.language or "en"  # 落库时为源默认语言（采集中枢 ingest）
            result = self.detector.detect_article(article.title, article.content, article.summary, source_default)
            article.language = result.language
            article.language_confidence = Decimal(str(round(result.confidence, 3)))
            metrics.low_confidence += int(result.low_confidence)
        metrics.language_ms = (time.perf_counter() - t0) * 1000

        # ② 跨语言向量化（批量推理）+ ③ pgvector 落库（embedding 随 articles 更新）
        t0 = time.perf_counter()
        texts = [build_embedding_text(a.title, a.summary, a.content) for a in articles]
        vectors = self.embedder.embed(texts)
        metrics.embed_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        for article, vector in zip(articles, vectors, strict=True):
            article.embedding = vector
        self.db.commit()  # 语言+向量先落 PG：ES 故障不影响对外可见
        metrics.db_ms = (time.perf_counter() - t0) * 1000
        metrics.per_article_ms = [
            (metrics.embed_ms + metrics.db_ms) / len(articles) for _ in articles
        ]

        # ④ ES 全文索引同步（最终一致，有界重试；失败抛 EsSyncError 由 worker 整批重投递）
        if self.es_indexer is not None:
            t0 = time.perf_counter()
            docs = [
                ArticleDoc(
                    article_id=a.id, title=a.title, content=a.content, summary=a.summary,
                    language=a.language, country_code=a.country_code, source_id=a.source_id,
                    source_channel=a.source_channel, published_at=a.published_at,
                )
                for a in articles
            ]
            self.es_indexer.index_articles(docs)
            metrics.es_ms = (time.perf_counter() - t0) * 1000

        # ⑤ 延迟埋点（published_at→visible_at 按源/通道分桶；article_id 唯一幂等）
        for article in articles:
            record_sample(
                self.db,
                article_id=article.id,
                source_id=article.source_id,
                channel=article.source_channel,
                country_code=article.country_code,
                published_at=article.published_at,
                visible_at=article.visible_at or article.crawled_at,
            )
        self.db.commit()

        metrics.processed = len(articles)
        logger.info(
            "nlp_batch_done",
            processed=metrics.processed,
            low_confidence=metrics.low_confidence,
            language_ms=round(metrics.language_ms, 1),
            embed_ms=round(metrics.embed_ms, 1),
            db_ms=round(metrics.db_ms, 1),
            es_ms=round(metrics.es_ms, 1),
        )
        return metrics
