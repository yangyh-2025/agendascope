"""persons_orgs 列表/详情 API（T4.4）。"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_REGISTERED, get_db, require_role
from app.core.errors import CODE_NOT_FOUND, BizError, ok
from app.models.person import PersonOrg
from app.models.user import User

router = APIRouter()


@router.get("")
def list_persons(
    entity_type: str | None = Query(None, description="person/thinktank/intl_org/gov_body"),
    country_code: str | None = Query(None, max_length=2),
    monitored: bool | None = Query(None),
    sort: str = Query(default="name", pattern="^(name|latest_utterance_at|created_at)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    stmt = select(PersonOrg)
    if entity_type:
        stmt = stmt.where(PersonOrg.entity_type == entity_type)
    if country_code:
        stmt = stmt.where(PersonOrg.country_code == country_code)
    if monitored is not None:
        stmt = stmt.where(PersonOrg.monitored == monitored)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    offset = (page - 1) * page_size
    if sort == "name":
        stmt = stmt.order_by(PersonOrg.name)
    elif sort == "created_at":
        stmt = stmt.order_by(PersonOrg.created_at.desc())
    else:  # latest_utterance_at：按最新首发表述时间降序（JSONB 数组取 max occurred_at）
        stmt = stmt.order_by(
            func.coalesce(
                func.jsonb_array_length(PersonOrg.first_utterances), 0
            ).desc(),
            PersonOrg.created_at.desc(),
        )
    entities = db.scalars(stmt.offset(offset).limit(page_size)).all()

    items = [{
        "id": str(e.id), "entity_type": e.entity_type, "name": e.name,
        "name_zh": e.name_zh, "country_code": e.country_code, "role_title": e.role_title,
        "monitored": e.monitored, "first_utterances": e.first_utterances,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in entities]

    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


@router.get("/{entity_id}")
def get_person(
    entity_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    entity = db.get(PersonOrg, entity_id)
    if entity is None:
        raise BizError(CODE_NOT_FOUND, f"实体不存在: {entity_id}")
    return ok({
        "id": str(entity.id), "entity_type": entity.entity_type, "name": entity.name,
        "name_zh": entity.name_zh, "name_aliases": entity.name_aliases,
        "country_code": entity.country_code, "role_title": entity.role_title,
        "monitored": entity.monitored, "first_utterances": entity.first_utterances,
        "created_at": entity.created_at.isoformat() if entity.created_at else None,
        "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
    })


__all__ = ["router"]
