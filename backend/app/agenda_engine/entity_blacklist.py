"""动态高频实体黑名单（T3.5，详细设计 4.2 算法 5）。

每日（外部调度器负责）统计近 N 天 articles 的实体频次，取 Top-K 写入
Redis Set `entity:blacklist`，供下游三处使用：
  ① 聚类特征加权：黑名单实体在 c-TF-IDF 与关联图边权中降权/剔除
  ② 议题归并比对：不因共享黑名单实体而提高相似度
  ③ 实体日频仍作议题萌芽/升温信号——黑名单只防"虚假关联"，不丢弃"趋势信号"

刷新失败保旧值：Redis 不可写时仅 log warning，不抛错（黑名单是优化而非正确性依赖）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.entity_extract import extract_entities, is_valid_entity
from app.core.logging import get_logger
from app.models.article import Article

if TYPE_CHECKING:
    import redis

logger = get_logger("agenda.entity_blacklist")


def _count_entities_in_window(
    db: Session,
    window_days: int,
    now: datetime | None = None,
) -> dict[str, int]:
    """统计近 window_days 天 articles 的实体文档频次。

    同一篇文章同一实体只计 1 次（文档频次口径），防止单篇长文刷量
    把非高频词顶入黑名单。
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)
    freq: dict[str, int] = {}

    stmt = select(Article.title, Article.content).where(Article.published_at >= cutoff)
    for title, content in db.execute(stmt):
        article_entities: set[str] = set()
        for text in (title, content):
            if not text:
                continue
            for entity_text, _kind in extract_entities(text):
                if is_valid_entity(entity_text):
                    article_entities.add(entity_text)
        for entity in article_entities:
            freq[entity] = freq.get(entity, 0) + 1
    return freq


def refresh_entity_blacklist(
    db: Session,
    redis_client: redis.Redis,
    top_k: int | None = None,
    window_days: int | None = None,
) -> list[str]:
    """统计近 window_days 天实体文档频次 → 取 Top-K 写 Redis Set。

    返回本次写入的黑名单实体列表（按频次降序）。Redis 刷新失败保旧值
    不抛错——保留旧 TTL 内的旧名单继续服务。
    """
    settings = get_agenda_settings()
    top_k = top_k if top_k is not None else settings.entity_blacklist_top_k
    window_days = (
        window_days if window_days is not None else settings.entity_blacklist_window_days
    )

    freq = _count_entities_in_window(db, window_days=window_days)
    # 频次降序，词文本字典序兜底（确定性输出，便于测试断言）
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    blacklist = [entity for entity, _count in ranked[:top_k]]

    try:
        pipe = redis_client.pipeline(transaction=True)
        pipe.delete(settings.entity_blacklist_key)
        if blacklist:
            pipe.sadd(settings.entity_blacklist_key, *blacklist)
        pipe.expire(
            settings.entity_blacklist_key,
            settings.entity_blacklist_ttl_hours * 3600,
        )
        pipe.set(settings.entity_blacklist_updated_at_key, datetime.now(UTC).isoformat())
        pipe.execute()
    except Exception as exc:  # noqa: BLE001  Redis 故障保旧值，不打断调度
        logger.warning(
            "entity_blacklist_refresh_redis_failed",
            error=str(exc),
            candidates=len(blacklist),
        )
        return blacklist

    logger.info(
        "entity_blacklist_refreshed",
        size=len(blacklist),
        window_days=window_days,
        top_k=top_k,
    )
    return blacklist


def get_entity_blacklist(redis_client: redis.Redis) -> set[str]:
    """读 Redis Set；不存在 / 读失败均返回空集合（黑名单缺位等于不过滤）。"""
    settings = get_agenda_settings()
    try:
        members = redis_client.smembers(settings.entity_blacklist_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("entity_blacklist_read_failed", error=str(exc))
        return set()
    return {str(m) for m in members} if members else set()


def is_blacklisted(entity: str, redis_client: redis.Redis) -> bool:
    """单实体黑名单查询（O(1)）。Redis 不可达按 False 处理（不过滤优于误过滤）。"""
    if not entity:
        return False
    settings = get_agenda_settings()
    try:
        return bool(redis_client.sismember(settings.entity_blacklist_key, entity))
    except Exception as exc:  # noqa: BLE001
        logger.warning("entity_blacklist_query_failed", error=str(exc))
        return False


def filter_blacklisted(
    entities: list[str],
    redis_client: redis.Redis,
) -> list[str]:
    """聚类 / 归并比对前过滤：返回剔除黑名单后的实体列表，保持原顺序、去重。

    实现侧先一次性读全集再本地过滤——比逐个 SISMEMBER 少 N-1 次 RTT；
    Redis 不可达时返回原列表（黑名单缺位降级为不过滤）。
    """
    if not entities:
        return []
    blacklist = get_entity_blacklist(redis_client)
    if not blacklist:
        return list(entities)
    seen: set[str] = set()
    kept: list[str] = []
    for entity in entities:
        if entity in blacklist or entity in seen:
            continue
        seen.add(entity)
        kept.append(entity)
    return kept
