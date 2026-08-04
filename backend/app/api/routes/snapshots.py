"""agenda_snapshots 显著性时间线 + 跨国对比 API（T4.4）。"""
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_AUTHORIZED, ROLE_REGISTERED, get_db, require_role
from app.core.errors import CODE_NOT_FOUND, BizError, ok
from app.models.snapshots import TopicSnapshot as AgendaSnapshot
from app.models.topic import Topic
from app.models.user import User

router = APIRouter()


@router.get("/topics/{topic_id}")
def topic_timeline(
    topic_id: uuid.UUID,
    countries: str | None = Query(None, description="逗号分隔，如 CN,US,JP"),
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise BizError(CODE_NOT_FOUND, f"议题不存在: {topic_id}")

    window_start = datetime.now(UTC) - timedelta(days=days)
    stmt = select(AgendaSnapshot).where(
        AgendaSnapshot.topic_id == topic_id,
        AgendaSnapshot.window_start >= window_start,
    ).order_by(AgendaSnapshot.window_start.asc())
    if countries:
        cc_list = [c.strip().upper() for c in countries.split(",") if c.strip()]
        stmt = stmt.where(AgendaSnapshot.country_code.in_(cc_list))

    snaps = db.scalars(stmt).all()
    by_country: dict[str, list] = {}
    for snap in snaps:
        by_country.setdefault(snap.country_code, []).append({
            "window_start": snap.window_start.isoformat(),
            "article_count": snap.article_count,
            "salience_score": float(snap.salience_score or 0),
            "salience_rank": snap.salience_rank,
        })
    return ok({
        "topic_id": str(topic.id),
        "topic_name": topic.name,
        "timeline": by_country,
    })


@router.get("/compare")
def compare_countries(
    countries: str = Query(..., description="逗号分隔 ≤4 国"),
    days: int = Query(7, ge=1, le=90),
    topic_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    cc_list = [c.strip().upper() for c in countries.split(",") if c.strip()]
    if len(cc_list) > 4:
        cc_list = cc_list[:4]
    window_start = datetime.now(UTC) - timedelta(days=days)

    per_country = []
    for cc in cc_list:
        stmt = select(AgendaSnapshot).where(
            AgendaSnapshot.window_start >= window_start,
            AgendaSnapshot.country_code == cc,
        ).order_by(AgendaSnapshot.window_start.asc())
        snaps = db.scalars(stmt).all()
        salience_curve = [
            {"window_start": s.window_start.isoformat(), "score": float(s.salience_score or 0), "article_count": s.article_count}
            for s in snaps
        ]
        total_articles = sum(s.article_count for s in snaps)
        top_topic_id = None
        top_topic_name = None
        if snaps:
            best = max(snaps, key=lambda s: float(s.salience_score or 0))
            top_topic_id = best.topic_id
            topic_obj = db.get(Topic, top_topic_id) if top_topic_id else None
            top_topic_name = topic_obj.name if topic_obj else None

        per_country.append({
            "country_code": cc,
            "salience_curve": salience_curve,
            "total_articles": total_articles,
            "top_topic_id": str(top_topic_id) if top_topic_id else None,
            "top_topic_name": top_topic_name,
            "coverage": "normal" if total_articles > 0 else "low",
        })

    return ok({
        "countries": cc_list,
        "days": days,
        "per_country": per_country,
        "disclaimer": "统计关联≠因果——显著性曲线仅反映不同国家对该议题的媒体报道相对强度，不能直接推断因果方向。",
    })


__all__ = ["router"]
