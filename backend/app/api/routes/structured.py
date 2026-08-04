"""v3.0 重构新增的查询 endpoint（全部走新维度表 SELECT）。

- /topics/{id}/lifecycle      议题生命周期历史
- /topics/{id}/keywords       议题关键词（从 topic_keywords 读）
- /topics/{id}/countries      议题涉及国家（从 topic_countries 读）
- /events/{id}/follow-chain   事件传播链（从 agenda_event_followers 读）
- /entities/{id}/timeline     实体提及曲线（从 entity_snapshots 读）
- /entities/{id}/articles     实体相关文章（从 article_entities 读）
- /processing/stats           加工流水线状态统计（从 article_processing 读）
"""
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_REGISTERED, get_db, require_role
from app.core.errors import CODE_NOT_FOUND, BizError, ok
from app.models.agenda import AgendaEvent
from app.models.agenda_event_dimensions import AgendaEventFollower
from app.models.article import Article
from app.models.article_entity import ArticleEntity
from app.models.person import PersonOrg
from app.models.processing import ArticleProcessing
from app.models.snapshots import EntitySnapshot
from app.models.source import Source
from app.models.topic import Topic
from app.models.topic_dimensions import TopicCountry, TopicKeyword, TopicLifecycleEvent
from app.models.user import User

router = APIRouter()


# --------------------------------------------------------------------------
# 议题维度
# --------------------------------------------------------------------------
@router.get("/topics/{topic_id}/lifecycle")
def get_topic_lifecycle(
    topic_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """议题生命周期变化历史。"""
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise BizError(CODE_NOT_FOUND, "议题不存在")
    stmt = (
        select(TopicLifecycleEvent)
        .where(TopicLifecycleEvent.topic_id == topic_id)
        .order_by(desc(TopicLifecycleEvent.created_at))
        .limit(limit)
    )
    events = db.scalars(stmt).all()
    return ok({
        "topic_id": str(topic_id),
        "topic_name": topic.name_zh or topic.name,
        "current_state": {
            "status": topic.status,
            "lifecycle_state": topic.lifecycle_state,
            "confidence": topic.confidence,
        },
        "total": len(events),
        "items": [{
            "id": str(e.id),
            "event_type": e.event_type,
            "from_value": e.from_value,
            "to_value": e.to_value,
            "actor": e.actor,
            "reason": e.reason,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        } for e in events],
    })


@router.get("/topics/{topic_id}/keywords")
def get_topic_keywords(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """议题关键词（按 rank 升序）。"""
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise BizError(CODE_NOT_FOUND, "议题不存在")
    stmt = (
        select(TopicKeyword)
        .where(TopicKeyword.topic_id == topic_id)
        .order_by(TopicKeyword.rank)
    )
    keywords = db.scalars(stmt).all()
    return ok({
        "topic_id": str(topic_id),
        "total": len(keywords),
        "items": [{
            "keyword": k.keyword,
            "weight": float(k.weight),
            "rank": k.rank,
            "source": k.source,
        } for k in keywords],
    })


@router.get("/topics/{topic_id}/countries")
def get_topic_countries(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """议题涉及国家分布。"""
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise BizError(CODE_NOT_FOUND, "议题不存在")
    stmt = (
        select(TopicCountry)
        .where(TopicCountry.topic_id == topic_id)
        .order_by(desc(TopicCountry.article_count))
    )
    countries = db.scalars(stmt).all()
    return ok({
        "topic_id": str(topic_id),
        "total": len(countries),
        "items": [{
            "country_code": c.country_code,
            "article_count": c.article_count,
            "salience_peak": float(c.salience_peak) if c.salience_peak else None,
            "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
            "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
        } for c in countries],
    })


# --------------------------------------------------------------------------
# 事件传播链
# --------------------------------------------------------------------------
@router.get("/events/{event_id}/follow-chain")
def get_event_follow_chain(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """事件传播链：首发源 → 各跟随源（按时序）。"""
    event = db.get(AgendaEvent, event_id)
    if event is None:
        raise BizError(CODE_NOT_FOUND, "事件不存在")
    stmt = (
        select(AgendaEventFollower, Source)
        .join(Source, Source.id == AgendaEventFollower.source_id)
        .where(AgendaEventFollower.event_id == event_id)
        .order_by(AgendaEventFollower.followed_at)
    )
    rows = db.execute(stmt).all()
    items = [{
        "id": str(f.id),
        "sequence_no": f.sequence_no,
        "source_id": str(f.source_id),
        "source_name": s.name_zh or s.name,
        "country_code": f.country_code,
        "article_id": str(f.article_id) if f.article_id else None,
        "followed_at": f.followed_at.isoformat() if f.followed_at else None,
        "lag_seconds": f.lag_seconds,
    } for f, s in rows]
    return ok({
        "event_id": str(event_id),
        "origin_at": event.origin_at.isoformat() if event.origin_at else None,
        "origin_country_code": event.origin_country_code,
        "total_followers": len(items),
        "items": items,
    })


# --------------------------------------------------------------------------
# 实体维度
# --------------------------------------------------------------------------
@router.get("/entities/{entity_id}/timeline")
def get_entity_timeline(
    entity_id: uuid.UUID,
    granularity: str = Query("day", pattern="^(hour|day|week)$"),
    days: int = Query(30, ge=1, le=180),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """实体提及曲线（entity_snapshots 时间序列）。"""
    entity = db.get(PersonOrg, entity_id)
    if entity is None:
        raise BizError(CODE_NOT_FOUND, "实体不存在")
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(EntitySnapshot)
        .where(
            EntitySnapshot.entity_id == entity_id,
            EntitySnapshot.granularity == granularity,
            EntitySnapshot.window_start >= cutoff,
        )
        .order_by(EntitySnapshot.window_start)
    )
    snaps = db.scalars(stmt).all()
    return ok({
        "entity_id": str(entity_id),
        "entity_name": entity.name_zh or entity.name,
        "granularity": granularity,
        "total": len(snaps),
        "items": [{
            "window_start": s.window_start.isoformat() if s.window_start else None,
            "window_end": s.window_end.isoformat() if s.window_end else None,
            "mention_count": s.mention_count,
            "article_count": s.article_count,
            "unique_sources": s.unique_sources,
            "sentiment_avg": float(s.sentiment_avg) if s.sentiment_avg is not None else None,
            "sentiment_pos": float(s.sentiment_pos) if s.sentiment_pos is not None else None,
            "sentiment_neg": float(s.sentiment_neg) if s.sentiment_neg is not None else None,
            "first_utterance_count": s.first_utterance_count,
            "relation_new_count": s.relation_new_count,
        } for s in snaps],
    })


@router.get("/entities/{entity_id}/articles")
def get_entity_articles(
    entity_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    primary_only: bool = Query(False, description="仅看实体为文章主角的报道"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """实体相关文章（从 article_entities JOIN articles）。"""
    entity = db.get(PersonOrg, entity_id)
    if entity is None:
        raise BizError(CODE_NOT_FOUND, "实体不存在")
    stmt = (
        select(ArticleEntity, Article)
        .join(Article, Article.id == ArticleEntity.article_id)
        .where(ArticleEntity.entity_id == entity_id)
    )
    if primary_only:
        stmt = stmt.where(ArticleEntity.is_primary_subject.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(desc(Article.published_at)).offset((page - 1) * page_size).limit(page_size)
    rows = db.execute(stmt).all()
    items = [{
        "article_id": str(a.id),
        "title": a.title,
        "title_translated": a.title_translated,
        "url": a.url,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "country_code": a.country_code,
        "language": a.language,
        "sentiment": a.sentiment,
        "mention_count": ae.mention_count,
        "is_primary_subject": ae.is_primary_subject,
        "extracted_by": ae.extracted_by,
        "confidence": float(ae.confidence),
    } for ae, a in rows]
    return ok({
        "entity_id": str(entity_id),
        "entity_name": entity.name_zh or entity.name,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    })


# --------------------------------------------------------------------------
# 加工流水线状态（系统层观察）
# --------------------------------------------------------------------------
@router.get("/processing/stats")
def get_processing_stats(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """加工流水线各阶段状态统计（看板 / 系统管理可用）。"""
    def _count(column, status):
        return db.scalar(select(func.count()).select_from(ArticleProcessing).where(column == status)) or 0

    stats = {}
    for stage, column in [
        ("nlp", ArticleProcessing.nlp_status),
        ("cluster", ArticleProcessing.cluster_status),
        ("entity_extract", ArticleProcessing.entity_extract_status),
        ("relation_extract", ArticleProcessing.relation_extract_status),
        ("translate", ArticleProcessing.translate_status),
    ]:
        stats[stage] = {
            "pending": _count(column, "pending"),
            "processing": _count(column, "processing"),
            "done": _count(column, "done"),
            "failed": _count(column, "failed"),
            "skipped": _count(column, "skipped"),
        }
    total_articles = db.scalar(select(func.count()).select_from(Article)) or 0
    total_tracked = db.scalar(select(func.count()).select_from(ArticleProcessing)) or 0
    return ok({
        "total_articles": total_articles,
        "total_tracked": total_tracked,
        "stages": stats,
    })


__all__ = ["router"]
