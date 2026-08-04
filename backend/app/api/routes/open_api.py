"""数据开放平台只读 API（详细设计 8.3）。

所有 endpoint 走 X-API-Key 鉴权（app.core.api_key_auth.get_api_key_user），
响应与主 API 一致：{code, data, message}。

只读，不做任何写操作。
"""
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.api_key_auth import get_api_key_user
from app.core.countries import COUNTRIES
from app.core.errors import CODE_NOT_FOUND, BizError, ok
from app.db.session import get_db
from app.models.agenda import AgendaEvent
from app.models.article import Article
from app.models.person import PersonOrg
from app.models.source import Source
from app.models.topic import AgendaSnapshot, Topic, TopicArticle
from app.models.user import User

router = APIRouter()


# --------------------------------------------------------------------------
# Topics
# --------------------------------------------------------------------------
@router.get("/topics")
def list_topics(
    status: str | None = Query(None),
    category: str | None = Query(None),
    q: str | None = Query(None, description="按议题名模糊匹配"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_api_key_user),
):
    stmt = select(Topic).where(Topic.merged_into.is_(None))
    if status:
        stmt = stmt.where(Topic.status == status)
    if category:
        stmt = stmt.where(Topic.topic_category == category)
    if q:
        stmt = stmt.where(Topic.name.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(desc(Topic.last_seen_at)).offset((page - 1) * page_size).limit(page_size)
    topics = db.scalars(stmt).all()
    items = [{
        "id": str(t.id),
        "name": t.name,
        "name_zh": t.name_zh,
        "category": t.topic_category,
        "summary_zh": t.summary_zh,
        "keywords": t.keywords,
        "status": t.status,
        "lifecycle_state": t.lifecycle_state,
        "confidence": t.confidence,
        "country_scope": t.country_scope,
        "first_seen_at": t.first_seen_at.isoformat() if t.first_seen_at else None,
        "last_seen_at": t.last_seen_at.isoformat() if t.last_seen_at else None,
    } for t in topics]
    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


@router.get("/topics/{topic_id}")
def get_topic(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_api_key_user),
):
    t = db.get(Topic, topic_id)
    if t is None:
        raise BizError(CODE_NOT_FOUND, "议题不存在")
    return ok({
        "id": str(t.id),
        "name": t.name,
        "name_zh": t.name_zh,
        "category": t.topic_category,
        "summary_zh": t.summary_zh,
        "keywords": t.keywords,
        "status": t.status,
        "lifecycle_state": t.lifecycle_state,
        "confidence": t.confidence,
        "country_scope": t.country_scope,
        "first_seen_at": t.first_seen_at.isoformat() if t.first_seen_at else None,
        "last_seen_at": t.last_seen_at.isoformat() if t.last_seen_at else None,
    })


# --------------------------------------------------------------------------
# Articles
# --------------------------------------------------------------------------
@router.get("/articles")
def list_articles(
    country_code: str | None = Query(None, max_length=2),
    topic_id: uuid.UUID | None = Query(None),
    source_id: uuid.UUID | None = Query(None),
    language: str | None = Query(None),
    hours: int = Query(24, ge=1, le=24 * 30, description="最近 N 小时，默认 24"),
    q: str | None = Query(None, description="标题模糊匹配"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_api_key_user),
):
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    stmt = select(Article).where(Article.published_at >= cutoff, Article.is_duplicate.is_(False))
    if country_code:
        stmt = stmt.where(Article.country_code == country_code.upper())
    if source_id:
        stmt = stmt.where(Article.source_id == source_id)
    if language:
        stmt = stmt.where(Article.language == language)
    if q:
        stmt = stmt.where(Article.title.ilike(f"%{q}%"))
    if topic_id:
        stmt = stmt.join(TopicArticle, TopicArticle.article_id == Article.id).where(
            TopicArticle.topic_id == topic_id
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(desc(Article.published_at)).offset((page - 1) * page_size).limit(page_size)
    articles = db.scalars(stmt).all()
    items = [{
        "id": str(a.id),
        "title": a.title,
        "title_translated": a.title_translated,
        "summary": a.summary,
        "url": a.url,
        "language": a.language,
        "country_code": a.country_code,
        "source_id": str(a.source_id),
        "sentiment": a.sentiment,
        "sentiment_score": float(a.sentiment_score) if a.sentiment_score is not None else None,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "source_channel": a.source_channel,
    } for a in articles]
    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


@router.get("/articles/{article_id}")
def get_article(
    article_id: uuid.UUID,
    include_content: bool = Query(True, description="是否返回完整正文"),
    db: Session = Depends(get_db),
    user: User = Depends(get_api_key_user),
):
    a = db.get(Article, article_id)
    if a is None:
        raise BizError(CODE_NOT_FOUND, "文章不存在")
    return ok({
        "id": str(a.id),
        "title": a.title,
        "title_translated": a.title_translated,
        "summary": a.summary,
        "content": a.content if include_content else None,
        "url": a.url,
        "language": a.language,
        "country_code": a.country_code,
        "source_id": str(a.source_id),
        "sentiment": a.sentiment,
        "sentiment_score": float(a.sentiment_score) if a.sentiment_score is not None else None,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "crawled_at": a.crawled_at.isoformat() if a.crawled_at else None,
        "source_channel": a.source_channel,
    })


# --------------------------------------------------------------------------
# Entities（persons_orgs 实体，阶段 C 含种子 50 精品）
# --------------------------------------------------------------------------
@router.get("/entities")
def list_entities(
    entity_type: str | None = Query(None, description="person/thinktank/intl_org/gov_body"),
    country_code: str | None = Query(None, max_length=2),
    monitored: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_api_key_user),
):
    stmt = select(PersonOrg)
    if entity_type:
        stmt = stmt.where(PersonOrg.entity_type == entity_type)
    if country_code:
        stmt = stmt.where(PersonOrg.country_code == country_code.upper())
    if monitored is not None:
        stmt = stmt.where(PersonOrg.monitored == monitored)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(PersonOrg.name).offset((page - 1) * page_size).limit(page_size)
    entities = db.scalars(stmt).all()
    items = [{
        "id": str(e.id),
        "entity_type": e.entity_type,
        "name": e.name,
        "name_zh": e.name_zh,
        "name_aliases": e.name_aliases,
        "country_code": e.country_code,
        "role_title": e.role_title,
        "monitored": e.monitored,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in entities]
    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


@router.get("/entities/{entity_id}")
def get_entity(
    entity_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_api_key_user),
):
    e = db.get(PersonOrg, entity_id)
    if e is None:
        raise BizError(CODE_NOT_FOUND, "实体不存在")
    return ok({
        "id": str(e.id),
        "entity_type": e.entity_type,
        "name": e.name,
        "name_zh": e.name_zh,
        "name_aliases": e.name_aliases,
        "country_code": e.country_code,
        "role_title": e.role_title,
        "monitored": e.monitored,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    })


# --------------------------------------------------------------------------
# Agenda events
# --------------------------------------------------------------------------
@router.get("/agenda-events")
def list_agenda_events(
    country_code: str | None = Query(None, max_length=2),
    status: str | None = Query(None),
    days: int = Query(30, ge=1, le=180),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_api_key_user),
):
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = select(AgendaEvent).where(AgendaEvent.created_at >= cutoff)
    if country_code:
        stmt = stmt.where(AgendaEvent.origin_country_code == country_code.upper())
    if status:
        stmt = stmt.where(AgendaEvent.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(desc(AgendaEvent.created_at)).offset((page - 1) * page_size).limit(page_size)
    events = db.scalars(stmt).all()
    items = [{
        "id": str(ev.id),
        "topic_id": str(ev.topic_id),
        "status": ev.status,
        "confidence": ev.confidence,
        "origin_type": ev.origin_type,
        "origin_country_code": ev.origin_country_code,
        "origin_at": ev.origin_at.isoformat() if ev.origin_at else None,
        "origin_confidence": ev.origin_confidence,
        "origin_quote": ev.origin_quote,
        "detection_method": ev.detection_method,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
    } for ev in events]
    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


# --------------------------------------------------------------------------
# Sources / Countries / Snapshots
# --------------------------------------------------------------------------
@router.get("/sources")
def list_sources(
    country_code: str | None = Query(None, max_length=2),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_api_key_user),
):
    stmt = select(Source)
    if country_code:
        stmt = stmt.where(Source.country_code == country_code.upper())
    if status:
        stmt = stmt.where(Source.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(Source.country_code, Source.name).offset((page - 1) * page_size).limit(page_size)
    sources = db.scalars(stmt).all()
    items = [{
        "id": str(s.id),
        "name": s.name,
        "name_zh": s.name_zh,
        "country_code": s.country_code,
        "homepage_url": s.homepage_url,
        "media_type": s.media_type,
        "language": s.language,
        "status": s.status,
        "coverage_confidence": s.coverage_confidence,
    } for s in sources]
    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


@router.get("/countries")
def list_countries(user: User = Depends(get_api_key_user)):
    items = [{"code": c.code, "name_zh": c.name_zh} for c in COUNTRIES]
    return ok({"total": len(items), "items": items})


@router.get("/snapshots")
def list_snapshots(
    topic_id: uuid.UUID | None = Query(None),
    country_code: str | None = Query(None, max_length=2),
    granularity: str = Query("day", pattern="^(hour|day|week)$"),
    days: int = Query(7, ge=1, le=90),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_api_key_user),
):
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = select(AgendaSnapshot).where(
        AgendaSnapshot.window_start >= cutoff,
        AgendaSnapshot.granularity == granularity,
    )
    if topic_id:
        stmt = stmt.where(AgendaSnapshot.topic_id == topic_id)
    if country_code:
        stmt = stmt.where(AgendaSnapshot.country_code == country_code.upper())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(desc(AgendaSnapshot.window_start)).offset((page - 1) * page_size).limit(page_size)
    snaps = db.scalars(stmt).all()
    items = [{
        "id": str(s.id),
        "topic_id": str(s.topic_id),
        "country_code": s.country_code,
        "window_start": s.window_start.isoformat() if s.window_start else None,
        "window_end": s.window_end.isoformat() if s.window_end else None,
        "granularity": s.granularity,
        "article_count": s.article_count,
        "salience_score": float(s.salience_score),
        "salience_rank": s.salience_rank,
        "sentiment_pos": float(s.sentiment_pos) if s.sentiment_pos is not None else None,
        "sentiment_neu": float(s.sentiment_neu) if s.sentiment_neu is not None else None,
        "sentiment_neg": float(s.sentiment_neg) if s.sentiment_neg is not None else None,
    } for s in snaps]
    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


__all__ = ["router"]
