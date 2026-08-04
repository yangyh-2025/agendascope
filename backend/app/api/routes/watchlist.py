"""监控对象关系图谱 endpoint（详细设计 9.3）。

- GET /watchlist/graph         返回节点+边供前端图谱渲染
- GET /watchlist/entities      种子实体列表
- GET /watchlist/entities/{id} 实体详情 + 关系
- GET /watchlist/relations/{relation_id}/evidences  边的证据列表
"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_REGISTERED, get_db, require_role
from app.core.errors import CODE_NOT_FOUND, BizError, ok
from app.models.article import Article
from app.models.entity_relation import EntityRelation, RelationEvidence
from app.models.person import PersonOrg
from app.models.source import Source
from app.models.user import User

router = APIRouter()


@router.get("/entities")
def list_watchlist_entities(
    category: str | None = Query(None),
    include_peripheral: bool = Query(False, description="是否含外围实体"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    stmt = select(PersonOrg)
    if include_peripheral:
        stmt = stmt.where((PersonOrg.is_seed.is_(True)) | (PersonOrg.category == "外围"))
    else:
        stmt = stmt.where(PersonOrg.is_seed.is_(True))
    if category:
        stmt = stmt.where(PersonOrg.category == category)
    stmt = stmt.order_by(PersonOrg.priority.desc(), PersonOrg.name)
    entities = db.scalars(stmt).all()
    items = [{
        "id": str(e.id),
        "name": e.name,
        "name_zh": e.name_zh,
        "entity_type": e.entity_type,
        "country_code": e.country_code,
        "role_title": e.role_title,
        "category": e.category,
        "is_seed": e.is_seed,
        "priority": e.priority,
    } for e in entities]
    return ok({"total": len(items), "items": items})


@router.get("/graph")
def get_watchlist_graph(
    include_peripheral: bool = Query(False),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    status: str = Query("active", pattern="^(active|expired|all)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """返回 ECharts graph 所需的 nodes/links。

    节点：种子实体（+ 可选外围实体）
    边：active 关系（置信度≥min_confidence）
    """
    entity_stmt = select(PersonOrg)
    if include_peripheral:
        entity_stmt = entity_stmt.where((PersonOrg.is_seed.is_(True)) | (PersonOrg.category == "外围"))
    else:
        entity_stmt = entity_stmt.where(PersonOrg.is_seed.is_(True))
    entities = db.scalars(entity_stmt).all()
    entity_map = {e.id: e for e in entities}

    rel_stmt = select(EntityRelation)
    if status != "all":
        rel_stmt = rel_stmt.where(EntityRelation.status == status)
    if min_confidence > 0:
        rel_stmt = rel_stmt.where(EntityRelation.confidence >= min_confidence)
    relations = db.scalars(rel_stmt).all()

    nodes = [{
        "id": str(e.id),
        "name": e.name_zh or e.name,
        "name_en": e.name,
        "entity_type": e.entity_type,
        "country_code": e.country_code,
        "role_title": e.role_title,
        "category": e.category or "其他",
        "is_seed": e.is_seed,
        "priority": e.priority,
    } for e in entities]

    links = []
    for r in relations:
        if r.subject_entity_id not in entity_map or r.object_entity_id not in entity_map:
            continue
        links.append({
            "id": str(r.id),
            "source": str(r.subject_entity_id),
            "target": str(r.object_entity_id),
            "relation_type": r.relation_type,
            "confidence": float(r.confidence),
            "evidence_count": r.evidence_count,
            "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        })

    return ok({
        "nodes": nodes,
        "links": links,
        "total_nodes": len(nodes),
        "total_links": len(links),
    })


@router.get("/entities/{entity_id}")
def get_watchlist_entity(
    entity_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    e = db.get(PersonOrg, entity_id)
    if e is None:
        raise BizError(CODE_NOT_FOUND, "实体不存在")
    # 该实体的所有 active 关系
    stmt = select(EntityRelation).where(
        (EntityRelation.subject_entity_id == entity_id)
        | (EntityRelation.object_entity_id == entity_id),
        EntityRelation.status == "active",
    )
    relations = db.scalars(stmt).all()
    rel_items = []
    for r in relations:
        other_id = r.object_entity_id if r.subject_entity_id == entity_id else r.subject_entity_id
        other = db.get(PersonOrg, other_id)
        rel_items.append({
            "relation_id": str(r.id),
            "direction": "outgoing" if r.subject_entity_id == entity_id else "incoming",
            "relation_type": r.relation_type,
            "other_entity": {
                "id": str(other.id),
                "name": other.name_zh or other.name,
                "entity_type": other.entity_type,
            } if other else None,
            "confidence": float(r.confidence),
            "evidence_count": r.evidence_count,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        })
    return ok({
        "id": str(e.id),
        "name": e.name,
        "name_zh": e.name_zh,
        "name_aliases": e.name_aliases,
        "entity_type": e.entity_type,
        "country_code": e.country_code,
        "role_title": e.role_title,
        "category": e.category,
        "is_seed": e.is_seed,
        "priority": e.priority,
        "relations": rel_items,
    })


@router.get("/relations/{relation_id}/evidences")
def get_relation_evidences(
    relation_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    rel = db.get(EntityRelation, relation_id)
    if rel is None:
        raise BizError(CODE_NOT_FOUND, "关系不存在")
    subject = db.get(PersonOrg, rel.subject_entity_id)
    obj = db.get(PersonOrg, rel.object_entity_id)

    stmt = (
        select(RelationEvidence, Article, Source)
        .join(Article, Article.id == RelationEvidence.article_id)
        .join(Source, Source.id == Article.source_id)
        .where(RelationEvidence.relation_id == relation_id)
        .order_by(RelationEvidence.published_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = db.execute(stmt).all()
    items = [{
        "evidence_id": str(ev.id),
        "article_id": str(art.id),
        "article_title": art.title,
        "article_title_translated": art.title_translated,
        "article_url": art.url,
        "source_name": src.name_zh or src.name,
        "source_country_code": src.country_code,
        "evidence_quote": ev.evidence_quote,
        "evidence_quote_zh": ev.evidence_quote_zh,
        "published_at": ev.published_at.isoformat() if ev.published_at else None,
    } for ev, art, src in rows]

    return ok({
        "relation": {
            "id": str(rel.id),
            "subject": {"id": str(subject.id), "name": subject.name_zh or subject.name} if subject else None,
            "object": {"id": str(obj.id), "name": obj.name_zh or obj.name} if obj else None,
            "relation_type": rel.relation_type,
            "confidence": float(rel.confidence),
            "evidence_count": rel.evidence_count,
        },
        "total": rel.evidence_count,
        "page": page,
        "page_size": page_size,
        "items": items,
    })


__all__ = ["router"]
