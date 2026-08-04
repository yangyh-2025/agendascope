"""articles 检索 API（T4.3）：ES 全文检索 + PG 降级，版权合规 L1 正文不出库。"""
import uuid
from datetime import UTC

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_REGISTERED, get_db, get_es, require_role
from app.core.errors import ok
from app.core.logging import get_logger
from app.models.article import Article
from app.models.source import Source
from app.models.user import User

router = APIRouter()

logger = get_logger("api.articles")

_EXCERPT_MAX = 150

REGISTERED_DAYS_LIMIT = 7

ES_INDEX = "articles"
ES_DEFAULT_SIZE = 20
ES_MAX_SIZE = 100


@router.get("")
def list_articles(
    request: Request,
    q: str | None = Query(None, description="全文搜索关键词"),
    country_code: str | None = Query(None, max_length=2),
    topic_id: uuid.UUID | None = Query(None, description="按议题过滤"),
    language: str | None = Query(None, max_length=10),
    date_from: str | None = Query(None, description="ISO 日期 开始"),
    date_to: str | None = Query(None, description="ISO 日期 结束"),
    page: int = Query(1, ge=1),
    page_size: int = Query(ES_DEFAULT_SIZE, ge=1, le=ES_MAX_SIZE),
    db: Session = Depends(get_db),
    es: Elasticsearch | None = Depends(get_es),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """ES 全文检索文章（标题+正文），版权合规 L1：仅返回标题 + ≤150 字摘录 + 原文链接。"""
    from datetime import datetime, timedelta

    # registered 角色仅可见近 7 天
    if user.role == ROLE_REGISTERED and not date_from:
        date_from = (datetime.now(UTC) - timedelta(days=REGISTERED_DAYS_LIMIT)).strftime("%Y-%m-%d")

    degraded = False
    degrade_reason = None

    article_ids_from_topic: list[uuid.UUID] | None = None
    if topic_id is not None:
        from app.models.topic import TopicArticle
        article_ids_from_topic = [
            row.article_id for row in db.execute(
                select(TopicArticle.article_id).where(TopicArticle.topic_id == topic_id)
            ).all()
        ]
        if not article_ids_from_topic:
            return ok({"total": 0, "page": page, "page_size": page_size, "items": [], "degraded": False})

    # ES 检索优先
    ids_from_es: list[str] | None = None
    if es is not None and q:
        try:
            body: dict = {
                "size": ES_MAX_SIZE * 2,
                "query": {"bool": {"must": [], "filter": []}},
                "_source": False,
            }
            if q:
                body["query"]["bool"]["must"].append({
                    "multi_match": {
                        "query": q, "fields": ["title^2", "content"],
                        "type": "best_fields", "tie_breaker": 0.3,
                    },
                })
            if country_code:
                body["query"]["bool"]["filter"].append({"term": {"country_code": country_code}})
            if language:
                body["query"]["bool"]["filter"].append({"term": {"language": language}})
            if date_from or date_to:
                range_filter: dict = {}
                if date_from:
                    range_filter["gte"] = date_from
                if date_to:
                    range_filter["lte"] = date_to
                body["query"]["bool"]["filter"].append({"range": {"published_at": range_filter}})
            if article_ids_from_topic:
                body["query"]["bool"]["filter"].append({"terms": {"_id": [str(aid) for aid in article_ids_from_topic]}})
            result = es.search(index=ES_INDEX, body=body)
            ids_from_es = [hit["_id"] for hit in result["hits"]["hits"]]
        except Exception as exc:  # noqa: BLE001 ES 不可用属预期降级场景，但必须留日志
            degraded = True
            degrade_reason = "es_unavailable"
            logger.warning(
                "articles_es_search_failed",
                error=str(exc)[:300], q=q, country_code=country_code,
            )

    # PG 查询（ES 可用时用 ES 的 ID 列表过滤；不可用时全文降级为 ILIKE）
    stmt = select(Article)
    if ids_from_es is not None:
        stmt = stmt.where(Article.id.in_([uuid.UUID(x) for x in ids_from_es]))
    elif q:
        stmt = stmt.where(
            or_(Article.title.ilike(f"%{q}%"), Article.content.ilike(f"%{q}%"))
        )
        degraded = True
        degrade_reason = "es_unavailable"
    if country_code:
        stmt = stmt.where(Article.country_code == country_code)
    if language:
        stmt = stmt.where(Article.language == language)
    if date_from:
        stmt = stmt.where(Article.published_at >= date_from)
    if date_to:
        stmt = stmt.where(Article.published_at <= date_to)
    if article_ids_from_topic:
        stmt = stmt.where(Article.id.in_(article_ids_from_topic))
    stmt = stmt.order_by(Article.published_at.desc())

    # 计数必须带全部过滤条件（修复：此前 total 为全表计数，分页/总数失真）
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

    offset = (page - 1) * page_size
    articles = db.scalars(stmt.offset(offset).limit(page_size)).all()

    source_ids = {a.source_id for a in articles if a.source_id}
    source_names: dict[uuid.UUID, str] = {}
    if source_ids:
        for sid, sname, sname_zh in db.execute(
            select(Source.id, Source.name, Source.name_zh).where(Source.id.in_(source_ids))
        ).all():
            source_names[sid] = sname_zh or sname

    items = []
    for a in articles:
        excerpt = ""
        if a.content:
            excerpt = a.content[: _EXCERPT_MAX].strip()
        items.append({
            "id": str(a.id),
            "title": a.title,
            "source_id": str(a.source_id) if a.source_id else None,
            "source_name": source_names.get(a.source_id),
            "country_code": a.country_code,
            "language": a.language,
            "published_at": a.published_at.isoformat() if a.published_at else None,
            "excerpt": excerpt,
            "url": a.url,
        })

    return ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "degraded": degraded,
        "degrade_reason": degrade_reason,
    })


__all__ = ["router"]
