"""pgvector 向量相似度检索接口（T2.3）。

embedding 随 articles 落库（vector(768)，HNSW 索引 idx_articles_embedding），
检索走 PostgreSQL 内 cosine distance（<= > 操作符），不引入独立向量库进程。
向量已由 Embedder L2 归一化，cosine distance = 1 - cosine similarity。
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import CODE_NOT_FOUND, BizError
from app.models.article import Article


@dataclass(frozen=True)
class SimilarArticle:
    article_id: UUID
    title: str
    language: str
    country_code: str
    published_at: datetime
    score: float  # cosine similarity, 越大越相似


def find_similar(
    db: Session,
    embedding: Sequence[float],
    top_n: int = 10,
    min_score: float = 0.0,
    exclude_id: UUID | None = None,
) -> list[SimilarArticle]:
    """按向量做 cosine Top-N 近邻检索（供在线增量归簇/转载合并调用）。"""
    distance = Article.embedding.cosine_distance(list(embedding))
    stmt = (
        select(Article, distance.label("distance"))
        .where(Article.embedding.is_not(None))
        .order_by(distance)
        .limit(top_n)
    )
    if min_score > 0:
        stmt = stmt.where(distance <= 1.0 - min_score)  # 阈值下推 SQL, 避免先截断后过滤漏结果
    if exclude_id is not None:
        stmt = stmt.where(Article.id != exclude_id)
    results = []
    for article, dist in db.execute(stmt).all():
        score = 1.0 - float(dist)
        results.append(
            SimilarArticle(
                article_id=article.id,
                title=article.title,
                language=article.language,
                country_code=article.country_code,
                published_at=article.published_at,
                score=score,
            )
        )
    return results


def find_similar_to_article(db: Session, article_id: UUID, top_n: int = 10, min_score: float = 0.0) -> list[SimilarArticle]:
    """以已入库文章为锚点检索近邻（排除自身）。"""
    article = db.get(Article, article_id)
    if article is None:
        raise BizError(CODE_NOT_FOUND, "文章不存在")
    if article.embedding is None:
        return []
    return find_similar(db, article.embedding, top_n=top_n, min_score=min_score, exclude_id=article.id)
