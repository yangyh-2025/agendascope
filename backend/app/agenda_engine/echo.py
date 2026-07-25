"""回声消除折叠（T3.1，详细设计 4.2 算法 1 + ADR-006）。

对议题内已归簇的报道集按 TIME_PUB 升序扫描：
  - 与既有 EchoNode 质心取最大余弦相似度；同日 ≥0.65 / 3 日内 ≥0.85 折叠
  - 折叠进 related_docs（保留全部来源），canonical 永远是最早 TIME_PUB
  - 质心按 time_decay_pool 时间衰减加权池化（非 mean pooling）
  - 跟风报道落库 is_duplicate=True + canonical_id 指向节点主记录

与在线 T_dup 判重分层：T_dup 在文章到达时实时拦截逐字转载；回声消除运行在议题内
已聚簇集合上，识别"语义跟风"（同一事件在不同国家的跟进报道），共同构成跟随
链路图的数据基础。绝不静默降级：所有相似度、fold_rule、国家、时间均全程留痕。
"""
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.config import get_agenda_settings
from app.clustering.repository import time_decay_pool
from app.core.logging import get_logger
from app.models.article import Article
from app.models.topic import TopicArticle

logger = get_logger("agenda_engine.echo")

FoldRule = Literal["same_day", "within_3d"]

# 阈值窗口（与算法 1 伪代码一致）：同日 ≤1d，3 日内 ≤3d；超过不折叠
_SAME_DAY = timedelta(days=1)
_WITHIN_3D = timedelta(days=3)


@dataclass
class RelatedDoc:
    """折叠进节点的跟风报道留痕（全部来源保留，供显著性权重与跨国跟随序列计算）。"""

    article_id: UUID
    similarity: float
    fold_rule: FoldRule


@dataclass
class EchoNode:
    """回声消除折叠节点：组内最早 TIME_PUB 报道为 canonical，跟风报道进 related_docs。"""

    canonical_article_id: UUID
    canonical_published_at: datetime
    earliest_pub: datetime  # = canonical_published_at（canonical 永远是最早，锚定首发源）
    centroid: list[float]
    countries: set[str] = field(default_factory=set)
    related_docs: list[RelatedDoc] = field(default_factory=list)


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；任一向量零范数返回 0（不参与折叠）。"""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _fold_threshold(dt: timedelta, fold_same_day: float, fold_3day: float) -> float | None:
    """按时间差取折叠阈值：同日 FOLD_SAME_DAY / 3 日内 FOLD_3DAY / 超过 None（不折叠）。"""
    if dt <= _SAME_DAY:
        return fold_same_day
    if dt <= _WITHIN_3D:
        return fold_3day
    return None


def _as_local_seconds(dt: timedelta) -> float:
    return max(dt.total_seconds(), 0.0)


def echo_fold_articles(
    articles: list[Article],
    *,
    fold_same_day: float | None = None,
    fold_3day: float | None = None,
) -> list[EchoNode]:
    """内存内对给定文章集合跑回声折叠（不写库，供测试与批量场景）。

    输入侧约定：
      - 跳过 embedding 为空的文章（无向量无法比对，返回侧也不留节点，由调用方保障输入已向量化）
      - 跳过 is_duplicate=True 的文章（已被在线判重折叠，不重复折叠；其 canonical_id 已落库）
      - published_at 升序扫描（最早者优先成为 canonical，锚定首发源）
    """
    settings = get_agenda_settings()
    thr_same = fold_same_day if fold_same_day is not None else settings.echo_fold_same_day
    thr_3day = fold_3day if fold_3day is not None else settings.echo_fold_3day

    candidates = [
        a for a in articles
        if a.embedding is not None and not a.is_duplicate
    ]
    candidates.sort(key=lambda a: a.published_at)

    nodes: list[EchoNode] = []
    for article in candidates:
        vec = [float(v) for v in article.embedding]
        # 与既有节点质心求最近邻（朴素 O(|D|·|N|)；生产侧议题内文章量有限，pgvector HNSW 优化后续接）
        best: EchoNode | None = None
        best_sim = 0.0
        for node in nodes:
            sim = _cosine(vec, node.centroid)
            if best is None or sim > best_sim:
                best = node
                best_sim = sim
        if best is None:
            nodes.append(EchoNode(
                canonical_article_id=article.id,
                canonical_published_at=article.published_at,
                earliest_pub=article.published_at,
                centroid=vec,
                countries={article.country_code},
            ))
            continue

        dt = article.published_at - best.earliest_pub
        threshold = _fold_threshold(dt, thr_same, thr_3day)
        if threshold is not None and best_sim >= threshold:
            fold_rule: FoldRule = "same_day" if dt <= _SAME_DAY else "within_3d"
            best.related_docs.append(RelatedDoc(
                article_id=article.id,
                similarity=round(best_sim, 6),
                fold_rule=fold_rule,
            ))
            best.countries.add(article.country_code)
            # 时间衰减加权池化：距首发越久，旧质心权重按半衰期衰减（IIS 教训：非 mean pooling）
            best.centroid = time_decay_pool(best.centroid, vec, _as_local_seconds(dt) / 3600.0)
            # 落库标记由调用方在 echo_fold_topic 路径统一处理；内存路径仅留痕于 EchoNode
        else:
            # 孤证微簇保留（议题萌芽）：size=1 EchoNode
            nodes.append(EchoNode(
                canonical_article_id=article.id,
                canonical_published_at=article.published_at,
                earliest_pub=article.published_at,
                centroid=vec,
                countries={article.country_code},
            ))
    return nodes


def echo_fold_topic(
    db: Session,
    topic_id: UUID,
    *,
    lookback_days: int | None = None,
) -> list[EchoNode]:
    """对议题下所有报道跑回声折叠并落库 is_duplicate/canonical_id。

    数据来源：topic_articles ⋈ articles，按 published_at 升序；窗口由 echo_lookback_days
    控制（议题活跃期内的报道参与判定，过老的报道已被生命周期流程归档）。

    落库口径：
      - 折叠进节点的文章：is_duplicate=True, canonical_id=节点 canonical_article_id
      - 未折叠（孤证或新风向）：保持原状，不改 is_duplicate
      - 已 is_duplicate=True 的输入由 echo_fold_articles 跳过，不会重复折叠

    返回节点集（含 related_docs 全部留痕），供调用方进一步算显著性/跟随链路。
    """
    settings = get_agenda_settings()
    days = lookback_days if lookback_days is not None else settings.echo_lookback_days
    cutoff = datetime.now(UTC) - timedelta(days=days)

    stmt = (
        select(Article)
        .join(TopicArticle, TopicArticle.article_id == Article.id)
        .where(
            TopicArticle.topic_id == topic_id,
            Article.embedding.is_not(None),
            Article.published_at >= cutoff,
        )
        .order_by(Article.published_at.asc())
    )
    articles = list(db.scalars(stmt).all())
    if not articles:
        logger.info("echo_fold_topic_empty", topic_id=str(topic_id), lookback_days=days)
        return []

    nodes = echo_fold_articles(articles)
    # 把折叠结果落库：related_docs 中每篇文章标记 is_duplicate + canonical_id 指向节点主记录
    by_id = {a.id: a for a in articles}
    folded = 0
    for node in nodes:
        for doc in node.related_docs:
            target = by_id.get(doc.article_id)
            if target is None:
                continue
            target.is_duplicate = True
            target.canonical_id = node.canonical_article_id
            folded += 1
    db.flush()
    logger.info(
        "echo_fold_topic_done",
        topic_id=str(topic_id),
        input_articles=len(articles),
        nodes=len(nodes),
        folded=folded,
    )
    return nodes
