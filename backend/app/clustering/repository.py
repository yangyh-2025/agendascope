"""topics/topic_articles 落库与生命周期初版（T2.10）+ 在线归簇的 PG 读写（T2.8）。

- 议题创建/更新、归属 upsert、重聚类迁移（assign_method 全程留痕）
- 生命周期初版：size=1 nascent（孤证微簇保留）/ <confirmed_min_size forming / ≥ confirmed
- 质心时间衰减加权池化（非 mean pooling，IIS 教训；供次日归并跨语言比对）
- "未归类"池 = 已向量化但无 topic_articles 归属的非重复文章（隐式池，不建额外表）
"""
import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.clustering.config import get_cluster_settings
from app.clustering.types import ClusterDoc
from app.models.article import Article
from app.models.topic import Topic, TopicArticle
from app.nlp.embedding import build_embedding_text

NAME_MAX_LEN = 280  # topics.name VARCHAR(300)，留余量


def lifecycle_for_size(size: int, confirmed_min_size: int | None = None) -> str:
    """生命周期初版口径：孤证 nascent → 有同伴 forming → 达确认规模 confirmed。"""
    threshold = confirmed_min_size or get_cluster_settings().confirmed_min_size
    if size <= 1:
        return "nascent"
    if size < threshold:
        return "forming"
    return "confirmed"


def time_decay_pool(
    old: list[float], new: list[float], dt_hours: float, half_life_hours: float | None = None
) -> list[float]:
    """时间衰减加权池化：距上次更新越久，旧质心权重按半衰期衰减，返回 L2 归一化结果。"""
    half_life = half_life_hours or get_cluster_settings().centroid_half_life_hours
    alpha = 0.5 ** (max(dt_hours, 0.0) / half_life)
    pooled = [alpha * o + (1 - alpha) * n for o, n in zip(old, new, strict=True)]
    norm = math.sqrt(sum(v * v for v in pooled))
    if norm > 0:
        pooled = [v / norm for v in pooled]
    return pooled


def load_window_docs(db: Session, window_hours: int, since: datetime | None = None) -> list[ClusterDoc]:
    """读取重聚类窗口内已向量化文章（时间升序，最早者优先成为代表报道锚点）。"""
    cutoff = since or (datetime.now(UTC) - timedelta(hours=window_hours))
    stmt = (
        select(Article)
        .where(Article.embedding.is_not(None), Article.published_at >= cutoff)
        .order_by(Article.published_at.asc())
    )
    return [
        ClusterDoc(
            article_id=a.id,
            title=a.title,
            text=build_embedding_text(a.title, a.summary, a.content),
            language=a.language,
            country_code=a.country_code,
            published_at=a.published_at,
            embedding=[float(v) for v in a.embedding],
        )
        for a in db.scalars(stmt).all()
    ]


def create_topic(
    db: Session,
    *,
    name_auto: str,
    keywords: list[str],
    cluster_method: str,
    centroid: list[float] | None,
    country_scope: list[str],
    lifecycle_state: str,
    naming_method: str = "ctfidf_fallback",
    topic_category: str | None = None,
    first_seen_at: datetime | None = None,
    last_seen_at: datetime | None = None,
) -> Topic:
    """建议题行；命名暂以代表标题/c-TF-IDF 兜底（naming_method 留痕，LLM 服务后续回填重命名）。"""
    now = datetime.now(UTC)
    topic = Topic(
        name=name_auto[:NAME_MAX_LEN],
        name_auto=name_auto[:NAME_MAX_LEN],
        naming_method=naming_method,
        keywords=keywords[: get_cluster_settings().ctfidf_top_n],
        cluster_method=cluster_method,
        centroid=centroid,
        country_scope=sorted(set(country_scope)),
        lifecycle_state=lifecycle_state,
        topic_category=topic_category,
        first_seen_at=first_seen_at or now,
        last_seen_at=last_seen_at or now,
    )
    db.add(topic)
    db.flush()
    return topic


def get_assignment(db: Session, article_id: UUID) -> TopicArticle | None:
    return db.scalar(select(TopicArticle).where(TopicArticle.article_id == article_id))


def assign_article(
    db: Session, topic: Topic, article_id: UUID, weight: float, assign_method: str
) -> bool:
    """归属 upsert（同议题重复归入幂等）；weight 截断 Numeric(4,3) 口径。返回是否新建归属。"""
    existing = db.get(TopicArticle, (topic.id, article_id))
    if existing is not None:
        existing.weight = round(min(max(weight, 0.0), 1.0), 3)
        existing.assign_method = assign_method
        db.flush()
        return False
    db.add(TopicArticle(
        topic_id=topic.id,
        article_id=article_id,
        weight=round(min(max(weight, 0.0), 1.0), 3),
        assign_method=assign_method,
    ))
    db.flush()
    return True


def move_assignment(db: Session, article_id: UUID, new_topic: Topic, weight: float, assign_method: str) -> bool:
    """重聚类校正：把文章从旧议题迁出再归入新议题（迁移留 assign_method 痕迹）。返回是否发生迁移。"""
    old = get_assignment(db, article_id)
    if old is not None and old.topic_id != new_topic.id:
        db.execute(delete(TopicArticle).where(TopicArticle.article_id == article_id))
        db.flush()
        assign_article(db, new_topic, article_id, weight, assign_method)
        return True
    if old is None:
        assign_article(db, new_topic, article_id, weight, assign_method)
    return False


def unassign_article(db: Session, article_id: UUID) -> bool:
    """校正判噪声：撤归属回"未归类"池。"""
    existing = get_assignment(db, article_id)
    if existing is None:
        return False
    db.delete(existing)
    db.flush()
    return True


def topic_size(db: Session, topic_id: UUID) -> int:
    return int(db.scalar(select(func.count()).select_from(TopicArticle).where(TopicArticle.topic_id == topic_id)) or 0)


def update_topic_on_assignment(
    db: Session,
    topic: Topic,
    article: Article,
    embedding: list[float],
    now: datetime | None = None,
) -> None:
    """归入后增量维护：质心时间衰减池化、国家集合并、last_seen_at、生命周期随规模推进。"""
    now = now or datetime.now(UTC)
    if topic.centroid is not None:
        dt_hours = (now - topic.last_seen_at).total_seconds() / 3600 if topic.last_seen_at else 0.0
        topic.centroid = time_decay_pool([float(v) for v in topic.centroid], embedding, dt_hours)
    else:
        topic.centroid = embedding
    countries = set(topic.country_scope or [])
    countries.add(article.country_code)
    topic.country_scope = sorted(countries)
    topic.last_seen_at = now
    size = topic_size(db, topic.id)
    # 生命周期只前进不后退（evolving/archived 由归并与消亡流程维护，不在此回退）
    target = lifecycle_for_size(size)
    order = ["nascent", "forming", "confirmed"]
    if topic.lifecycle_state in order and order.index(target) > order.index(topic.lifecycle_state):
        topic.lifecycle_state = target
    db.flush()


def nearest_active_topic(
    db: Session, embedding: list[float], min_score: float, active_days: int | None = None,
    now: datetime | None = None,
) -> tuple[Topic, float] | None:
    """活跃议题质心 HNSW 最近邻（cosine ≥ min_score）；排除已归并/已归档议题。

    now：活跃窗口的时间基准（缺省墙钟 now）；回放/测试注入模拟时间（如文章
    published_at），使历史时间轴上的议题在自身时间轴内保持"活跃"可比。
    """
    days = active_days or get_cluster_settings().active_topic_days
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    distance = Topic.centroid.cosine_distance(list(embedding))
    stmt = (
        select(Topic, distance.label("distance"))
        .where(
            Topic.centroid.is_not(None),
            Topic.merged_into.is_(None),
            Topic.lifecycle_state != "archived",
            Topic.last_seen_at >= cutoff,
        )
        .order_by(distance)
        .limit(1)
    )
    row = db.execute(stmt).first()
    if row is None:
        return None
    topic, dist = row
    score = 1.0 - float(dist)
    if score < min_score:
        return None
    return topic, score


def active_topics(db: Session, active_days: int | None = None) -> list[Topic]:
    days = active_days or get_cluster_settings().active_topic_days
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = select(Topic).where(
        Topic.merged_into.is_(None),
        Topic.lifecycle_state != "archived",
        Topic.last_seen_at >= cutoff,
    )
    return list(db.scalars(stmt).all())


def unclassified_articles(db: Session, older_than_hours: int | None = None) -> list[Article]:
    """"未归类"池：已向量化、非重复、无任何议题归属的文章（可按滞留时长过滤）。"""
    assigned = select(TopicArticle.article_id)
    stmt = select(Article).where(
        Article.embedding.is_not(None),
        Article.is_duplicate.is_(False),
        Article.id.not_in(assigned),
    )
    if older_than_hours is not None:
        cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
        stmt = stmt.where(Article.published_at <= cutoff)
    return list(db.scalars(stmt.order_by(Article.published_at.asc())).all())


def load_no_merge_pairs(db: Session) -> set[tuple[UUID, UUID]]:
    """∪ topics.no_merge_with 双向展开为无序对集合（frozenset 语义）。

    供次日归并（merge.py）与重聚类校正（recluster.py）共用：人工误并回滚名单
    一律先排除，机器永不把已人工拆开的两个议题自动合并回去。
    返回 set of (min_id, max_id) 元组（按 UUID 字节序规范化），便于 O(1) 查。
    """
    stmt = select(Topic.id, Topic.no_merge_with).where(Topic.no_merge_with.is_not(None))
    pairs: set[tuple[UUID, UUID]] = set()
    for tid, partners in db.execute(stmt).all():
        if not partners:
            continue
        for partner_raw in partners:
            try:
                partner = UUID(str(partner_raw))
            except (ValueError, TypeError):
                continue
            pair = (tid, partner) if tid.bytes <= partner.bytes else (partner, tid)
            pairs.add(pair)
    return pairs


def norm_pair(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    return (a, b) if a.bytes <= b.bytes else (b, a)


def archive_empty_topics(db: Session) -> int:
    """校正后空壳议题（文章被全部迁走）归档，保留可查不物理删除。"""
    topics = db.scalars(select(Topic).where(Topic.lifecycle_state != "archived")).all()
    archived = 0
    for topic in topics:
        if topic_size(db, topic.id) == 0:
            topic.lifecycle_state = "archived"
            archived += 1
    db.flush()
    return archived


def representative_titles(db: Session, topic_id: UUID, limit: int) -> list[str]:
    """簇内代表标题（归属权重降序）：LLM 命名器输入（5–10 条）与快照展示共用。"""
    stmt = (
        select(Article.title)
        .join(TopicArticle, TopicArticle.article_id == Article.id)
        .where(TopicArticle.topic_id == topic_id)
        .order_by(TopicArticle.weight.desc(), Article.published_at.asc())
        .limit(limit)
    )
    return [str(t) for t in db.scalars(stmt).all()]
