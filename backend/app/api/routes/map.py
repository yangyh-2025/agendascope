"""地图聚合 API（T4.5）：108 国×Top 议题一次性下发，首屏 ≤3s 预算。"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_REGISTERED, get_db, require_role
from app.core.countries import all_name_zh_map
from app.core.errors import ok
from app.models.article import Article
from app.models.topic import AgendaSnapshot, Topic
from app.models.user import User

router = APIRouter()

# 国家中文名统一从 countries.py 派生（单一事实源）
_COUNTRY_NAMES = all_name_zh_map()

_MAX_TOP_TOPICS = 5
_MIN_COVERAGE = 0.7


@router.get("/countries")
def map_countries(
    date: str | None = Query(None, description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    target_date = date or datetime.now(UTC).strftime("%Y-%m-%d")
    window_start = f"{target_date}T00:00:00"
    window_end = f"{target_date}T23:59:59"

    # 各国今日文章量
    rows = db.execute(
        select(
            Article.country_code,
            func.count(Article.id),
        )
        .where(
            Article.published_at >= window_start,
            Article.published_at <= window_end,
        )
        .group_by(Article.country_code)
    ).all()
    cc_counts: dict[str, int] = {row[0]: int(row[1]) for row in rows}

    # 各国最新快照的 Top 议题
    snaps = db.scalars(
        select(AgendaSnapshot).where(
            AgendaSnapshot.window_start >= window_start,
            AgendaSnapshot.window_start <= window_end,
        ).order_by(AgendaSnapshot.salience_rank.asc())
    ).all()

    # 批量取议题名（修复：此前循环内 db.get(Topic) 为 N+1 查询）
    topic_ids = {snap.topic_id for snap in snaps if snap.topic_id}
    topic_names: dict = {}
    if topic_ids:
        for t in db.scalars(select(Topic).where(Topic.id.in_(topic_ids))).all():
            topic_names[t.id] = t.name

    by_country: dict[str, list] = {}
    for snap in snaps:
        lst = by_country.setdefault(snap.country_code, [])
        if len(lst) >= _MAX_TOP_TOPICS:
            continue
        lst.append({
            "topic_id": str(snap.topic_id) if snap.topic_id else None,
            "name": topic_names.get(snap.topic_id),
            "salience_score": float(snap.salience_score or 0),
            "article_count": snap.article_count,
        })

    latest_visible = db.scalar(
        select(Article.visible_at).where(Article.visible_at.is_not(None)).order_by(Article.visible_at.desc()).limit(1)
    )
    now = datetime.now(UTC)
    data_delay_minutes = 0
    if latest_visible:
        data_delay_minutes = max(0, int((now - latest_visible).total_seconds() / 60))

    # 覆盖率置信度：以监控目标国清单为分母（修复：此前以"有数据国家数"为分母导致恒≈1）
    target_with_data = len([
        cc for cc in _COUNTRY_NAMES
        if cc_counts.get(cc, 0) > 0 or cc in by_country
    ])
    coverage_confidence = target_with_data / len(_COUNTRY_NAMES)

    # 无数据的目标国家也要下发（empty 标记，前端置灰；不冒充旧数据）
    all_countries = sorted(set(_COUNTRY_NAMES) | set(cc_counts) | set(by_country))
    items = []
    for cc in all_countries:
        article_count = cc_counts.get(cc, 0)
        top_topics = by_country.get(cc, [])
        has_data = article_count > 0 or bool(top_topics)
        items.append({
            "country_code": cc,
            "country_name_zh": _COUNTRY_NAMES.get(cc, cc),
            "article_count_today": article_count,
            "top_topics": top_topics,
            "coverage_confidence": round(coverage_confidence, 2),
            "degraded": coverage_confidence < _MIN_COVERAGE,
            "empty": not has_data,
            "data_delay_minutes": data_delay_minutes,
        })

    return ok({
        "items": items,
        "data_delay_minutes": data_delay_minutes,
        "coverage_confidence": round(coverage_confidence, 2),
    })


__all__ = ["router"]
