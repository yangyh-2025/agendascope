"""关键词匹配降级链（T2.11）：BERTopic/Agglomerative 均不可用时的粗聚类兜底。

- 历史议题 keywords 重叠匹配：新文章 token 与活跃议题 keywords 重叠达阈值归入该议题
- 国家-主题词典：无历史议题可匹配时按预置主题词表粗分建 keyword_fallback 议题
- 降级生效：cluster_method=keyword_fallback 标记 + 写 alerts 表 P1 告警（防抖 1h）
  + Redis 降级旗标记录起始时刻；恢复后重聚类覆盖降级窗口回填并清旗标
绝不静默降级：WARN 日志带 component/fallback/reason/since（详细设计日志规范）。
"""
from datetime import UTC, datetime

import redis as redis_lib
from sqlalchemy.orm import Session

from app.clustering.config import get_cluster_settings
from app.clustering.repository import (
    active_topics,
    assign_article,
    create_topic,
    lifecycle_for_size,
    topic_size,
)
from app.clustering.tokenize import tokenize, top_keywords
from app.core.logging import get_logger
from app.models.alert import Alert, AlertRule
from app.models.article import Article
from app.models.topic import Topic
from app.services.seed_service import ensure_admin

logger = get_logger("clustering.fallback")

SYSTEM_CLUSTER_FALLBACK_RULE = "系统-聚类降级监控"

# 国家-主题词典：主题类目 → 匹配词表（中英双语核心词；类目与详细设计预置分类体系一致）
THEME_LEXICON: dict[str, list[str]] = {
    "政治安全": ["政府", "选举", "议会", "总统", "外交", "制裁", "条约", "minister", "parliament", "election",
                 "president", "sanction", "diplomacy", "government", "senate", "policy"],
    "经济金融": ["经济", "央行", "降息", "加息", "通胀", "股市", "债券", "贸易", "关税", "economy", "inflation",
                 "central bank", "rate", "stock", "market", "trade", "tariff", "gdp", "recession"],
    "军事": ["军事", "军演", "导弹", "军舰", "国防", "military", "missile", "navy", "army", "defense",
             "troops", "weapon", "war", "strike"],
    "科技": ["科技", "芯片", "人工智能", "半导体", "航天", "technology", "chip", "semiconductor", "ai",
             "satellite", "space", "cyber", "quantum"],
    "能源气候": ["能源", "石油", "天然气", "气候", "碳排放", "核电", "energy", "oil", "gas", "climate",
                 "carbon", "nuclear", "renewable", "emission", "solar", "wind", "hydrogen"],
    "社会民生": ["社会", "民生", "教育", "医疗", "疫情", "就业", "society", "education", "health",
                 "hospital", "employment", "protest", "strike", "flood", "earthquake"],
}


def ensure_cluster_fallback_rule(db: Session) -> AlertRule:
    """系统内置降级监控规则（幂等），P1 告警挂靠此规则写入 alerts 表。"""
    from sqlalchemy import select

    rule = db.scalar(select(AlertRule).where(AlertRule.name == SYSTEM_CLUSTER_FALLBACK_RULE))
    if rule is not None:
        return rule
    admin = ensure_admin(db)
    rule = AlertRule(
        user_id=admin.id,
        name=SYSTEM_CLUSTER_FALLBACK_RULE,
        country_codes=[],
        keywords=["__cluster_fallback__"],
        condition_type="growth_rate",
        condition_value=0,
        notify_channels=["inapp"],
    )
    db.add(rule)
    db.flush()
    return rule


def alert_p1(db: Session, redis_client: redis_lib.Redis | None, reason: str) -> bool:
    """聚类降级 P1 告警：写 alerts 表（1h 防抖）。返回是否实际写入。"""
    settings = get_cluster_settings()
    if redis_client is not None:
        debounce_key = "alert:cluster_fallback"
        if redis_client.exists(debounce_key):
            return False
        redis_client.setex(debounce_key, settings.alert_debounce_seconds, "1")
    rule = ensure_cluster_fallback_rule(db)
    db.add(Alert(
        rule_id=rule.id,
        user_id=rule.user_id,
        payload={
            "kind": "cluster_fallback",
            "level": "P1",
            "component": "nlp_pipeline",
            "fallback": "keyword_fallback",
            "reason": reason[:500],
            "since": datetime.now(UTC).isoformat(),
        },
    ))
    db.flush()
    logger.warning("cluster_fallback_alert", component="nlp_pipeline", fallback="keyword_fallback", reason=reason[:200])
    return True


def set_degraded(redis_client: redis_lib.Redis, reason: str) -> None:
    """记录降级起始时刻（只在首次进入时写，回填窗口据此计算）。"""
    settings = get_cluster_settings()
    key = settings.degraded_flag_key
    if not redis_client.exists(key):
        redis_client.set(key, datetime.now(UTC).isoformat())
    logger.warning("cluster_degraded", component="nlp_pipeline", fallback="keyword_fallback", reason=reason[:200])


def degraded_since(redis_client: redis_lib.Redis) -> datetime | None:
    settings = get_cluster_settings()
    raw = redis_client.get(settings.degraded_flag_key)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def clear_degraded(redis_client: redis_lib.Redis) -> None:
    redis_client.delete(get_cluster_settings().degraded_flag_key)


def _match_topic_by_keywords(tokens: set[str], topics: list[Topic], min_overlap: int) -> tuple[Topic | None, int]:
    best: Topic | None = None
    best_overlap = 0
    for topic in topics:
        overlap = len(tokens & set(topic.keywords or []))
        if overlap > best_overlap:
            best, best_overlap = topic, overlap
    if best is not None and best_overlap >= min_overlap:
        return best, best_overlap
    return None, best_overlap


def _match_theme(tokens: set[str]) -> str | None:
    best_theme: str | None = None
    best_hits = 0
    for theme, words in THEME_LEXICON.items():
        hits = len(tokens & set(words))
        if hits > best_hits:
            best_theme, best_hits = theme, hits
    return best_theme if best_hits >= 1 else None


def assign_by_keywords(
    db: Session,
    articles: list[Article],
    *,
    create_topics: bool,
    min_overlap: int | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """关键词粗聚类：先匹配历史议题 keywords，无匹配再按国家-主题词典建/入兜底议题。

    返回统计 {matched, theme_created, theme_assigned, pooled}。议题统一打
    cluster_method=keyword_fallback 标记（恢复后回填据此识别降级期产出）。
    """
    settings = get_cluster_settings()
    overlap_threshold = min_overlap or settings.keyword_min_overlap
    now = now or datetime.now(UTC)
    topics = active_topics(db)
    theme_topics: dict[str, Topic] = {
        t.topic_category: t for t in topics if t.cluster_method == "keyword_fallback" and t.topic_category
    }
    stats = {"matched": 0, "theme_created": 0, "theme_assigned": 0, "pooled": 0}

    for article in articles:
        tokens = set(tokenize(f"{article.title}\n{article.summary or article.content or ''}"))
        topic, overlap = _match_topic_by_keywords(tokens, topics, overlap_threshold)
        if topic is not None:
            weight = min(1.0, overlap / max(len(topic.keywords or []), 1))
            assign_article(db, topic, article.id, weight, "recluster")
            topic.last_seen_at = now
            topic.lifecycle_state = lifecycle_for_size(topic_size(db, topic.id))
            stats["matched"] += 1
            continue
        if not create_topics:
            stats["pooled"] += 1
            continue
        theme = _match_theme(tokens)
        if theme is None:
            stats["pooled"] += 1
            continue
        topic = theme_topics.get(theme)
        if topic is None:
            topic = create_topic(
                db,
                name_auto=f"关键词兜底：{theme}",
                keywords=top_keywords([f"{article.title}\n{article.summary or article.content or ''}"], limit=10),
                cluster_method="keyword_fallback",
                naming_method="keyword_fallback",
                topic_category=theme,
                centroid=None,
                country_scope=[article.country_code],
                lifecycle_state="nascent",
                first_seen_at=article.published_at,
                last_seen_at=now,
            )
            theme_topics[theme] = topic
            stats["theme_created"] += 1
        assign_article(db, topic, article.id, 0.5, "recluster")
        countries = set(topic.country_scope or [])
        countries.add(article.country_code)
        topic.country_scope = sorted(countries)
        topic.last_seen_at = now
        topic.lifecycle_state = lifecycle_for_size(topic_size(db, topic.id))
        stats["theme_assigned"] += 1
    db.flush()
    return stats


def run_fallback(
    db: Session,
    redis_client: redis_lib.Redis | None,
    articles: list[Article],
    reason: str,
) -> dict[str, int]:
    """降级入口：关键词粗聚类 + P1 告警 + 降级旗标（供恢复后回填定位窗口）。"""
    if redis_client is not None:
        set_degraded(redis_client, reason)
        alert_p1(db, redis_client, reason)
    return assign_by_keywords(db, articles, create_topics=True)
