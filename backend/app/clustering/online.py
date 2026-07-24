"""在线增量双阈值归簇（T2.8，详细设计 4.2 算法 2）。

每篇已向量化文章到达时：
  ① T_dup=0.95 判重：与近期文章 HNSW 近邻比对，命中标记转载（is_duplicate/canonical_id），
     归入原文所在议题共享归属（跟风报道是议程跟随证据，不丢弃），不重复建簇
  ② T_event=0.85 归簇：与活跃议题质心 HNSW 比对，命中则归入并增量维护质心/生命周期
  ③ 都不命中：建 size=1 nascent 孤证微簇（议题萌芽保留，等待后续证据）

幂等：已归属文章直接跳过（worker 重投递/重复投递不重复建簇）。
"""
import time
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clustering.config import get_cluster_settings
from app.clustering.repository import (
    assign_article,
    create_topic,
    get_assignment,
    lifecycle_for_size,
    nearest_active_topic,
    update_topic_on_assignment,
)
from app.clustering.tokenize import top_keywords
from app.core.logging import get_logger
from app.models.article import Article
from app.models.topic import Topic, TopicArticle
from app.nlp.similarity import find_similar

logger = get_logger("clustering.online")

OUTCOME_DUPLICATE = "duplicate"
OUTCOME_ASSIGNED = "assigned"
OUTCOME_NEW_MICRO = "new_micro"
OUTCOME_UNCLASSIFIED = "unclassified"  # 转载原文本身未归类时，跟风报道一并留池
OUTCOME_SKIPPED = "skipped"


@dataclass(frozen=True)
class AssignmentOutcome:
    article_id: UUID
    outcome: str
    topic_id: UUID | None
    score: float
    duration_ms: float


class OnlineAssigner:
    def __init__(self, t_event: float | None = None, t_dup: float | None = None):
        settings = get_cluster_settings()
        self.settings = settings
        self.t_event = t_event if t_event is not None else settings.t_event
        self.t_dup = t_dup if t_dup is not None else settings.t_dup

    def assign(self, db: Session, article: Article) -> AssignmentOutcome:
        t0 = time.perf_counter()
        outcome = self._assign(db, article)
        duration_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "online_assign",
            article_id=str(article.id), outcome=outcome.outcome,
            topic_id=str(outcome.topic_id) if outcome.topic_id else None,
            score=round(outcome.score, 3), duration_ms=round(duration_ms, 1),
        )
        return AssignmentOutcome(
            article_id=article.id, outcome=outcome.outcome,
            topic_id=outcome.topic_id, score=outcome.score, duration_ms=duration_ms,
        )

    def _assign(self, db: Session, article: Article) -> AssignmentOutcome:
        done = AssignmentOutcome(article.id, OUTCOME_SKIPPED, None, 0.0, 0.0)
        if article.embedding is None:
            return done
        existing = get_assignment(db, article.id)
        if existing is not None:
            return AssignmentOutcome(article.id, OUTCOME_SKIPPED, existing.topic_id, float(existing.weight), 0.0)
        embedding = [float(v) for v in article.embedding]

        # ① 判重：与近期文章比对（pgvector HNSW，min_score 阈值下推 SQL）；
        #    转载只指向原始报道（跳过已判转载的候选，不形成转载链）
        canonical = None
        for candidate in find_similar(db, embedding, top_n=5, min_score=self.t_dup, exclude_id=article.id):
            candidate_article = db.get(Article, candidate.article_id)
            if candidate_article is not None and not candidate_article.is_duplicate:
                canonical = candidate
                break
        if canonical is not None:
            article.is_duplicate = True
            article.canonical_id = canonical.article_id
            canonical_assignment = db.scalar(
                select(TopicArticle).where(TopicArticle.article_id == canonical.article_id)
            )
            if canonical_assignment is not None:
                topic = db.get(Topic, canonical_assignment.topic_id)
                if topic is not None:
                    assign_article(db, topic, article.id, canonical.score, "online")
                    update_topic_on_assignment(db, topic, article, embedding)
                    db.flush()
                    return AssignmentOutcome(article.id, OUTCOME_DUPLICATE, topic.id, canonical.score, 0.0)
            db.flush()
            return AssignmentOutcome(article.id, OUTCOME_UNCLASSIFIED, None, canonical.score, 0.0)

        # ② 归簇：活跃议题质心最近邻
        hit = nearest_active_topic(db, embedding, min_score=self.t_event)
        if hit is not None:
            topic, score = hit
            assign_article(db, topic, article.id, score, "online")
            update_topic_on_assignment(db, topic, article, embedding)
            db.flush()
            return AssignmentOutcome(article.id, OUTCOME_ASSIGNED, topic.id, score, 0.0)

        # ③ 孤证微簇：size=1 nascent 保留（议题萌芽不丢弃）
        keywords = top_keywords([f"{article.title}\n{article.summary or article.content or ''}"], limit=10)
        topic = create_topic(
            db,
            name_auto=article.title,
            keywords=keywords,
            cluster_method="agglomerative",  # 在线硬阈值归簇与 Agglomerative 同族（枚举无 online 值）
            centroid=embedding,
            country_scope=[article.country_code],
            lifecycle_state=lifecycle_for_size(1),
            first_seen_at=article.published_at,
            last_seen_at=article.published_at,
        )
        assign_article(db, topic, article.id, 1.0, "online")
        db.flush()
        return AssignmentOutcome(article.id, OUTCOME_NEW_MICRO, topic.id, 1.0, 0.0)
