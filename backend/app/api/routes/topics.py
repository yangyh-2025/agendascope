"""topics 模块端点（详细设计 1.7 议题列表/详情/时间线/合并建议/重命名/分裂）。"""
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.orm import Session

from app.agenda_engine.split import SplitError, split_topic
from app.api.deps import ROLE_AUTHORIZED, ROLE_REGISTERED, require_license_active, require_role
from app.core.countries import all_codes as _all_country_codes
from app.core.errors import (
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    CODE_PARAM_INVALID,
    CODE_STATE_INVALID,
    BizError,
    ok,
)
from app.db.redis_client import get_cache_redis
from app.db.session import get_db
from app.models.agenda import AgendaEvent
from app.models.article import Article
from app.models.topic import AgendaSnapshot, Topic, TopicArticle
from app.models.user import User
from app.repositories.audit_repo import write_audit
from app.schemas.topic import TopicRenameRequest

router = APIRouter()

# registered 角色默认可访问的国家（PRD 4.1：registered 限 3 国）
_REGISTERED_DEFAULT_COUNTRIES = {"CN", "US", "JP"}
# 全量授权国家：从 countries.py 单一事实源派生（覆盖 G20 + 全球南方全部监控国）
_AUTHORIZED_ALL_COUNTRIES = set(_all_country_codes())

_TOPIC_CATEGORIES = {
    "政治安全", "经济金融", "军事", "科技", "能源气候", "社会民生", "其他",
}
_LIFECYCLE_STATES = {"nascent", "forming", "confirmed", "evolving", "archived"}
_SORTS = {"salience", "last_seen_at", "article_count"}


def _validate_date(value: str | None) -> date | None:
    """date 查询参数：YYYY-MM-DD，非法抛 1001。"""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise BizError(CODE_PARAM_INVALID, f"date 格式非法: {value}（要求 YYYY-MM-DD）") from None


def _check_country_scope(user: User, country_code: str | None) -> None:
    """registered 仅可访问默认 3 国；authorized/admin 全量。"""
    if user.role == ROLE_REGISTERED and country_code and country_code.upper() not in _REGISTERED_DEFAULT_COUNTRIES:
        raise BizError(
                CODE_FORBIDDEN,
                f"registered 角色仅可访问 {sorted(_REGISTERED_DEFAULT_COUNTRIES)} 国家数据",
            )


def _allowed_countries(user: User) -> set[str] | None:
    """返回该用户可访问的国家集合；None 表示不限制。"""
    if user.role == ROLE_REGISTERED:
        return _REGISTERED_DEFAULT_COUNTRIES
    return None


def _topic_brief(topic: Topic) -> dict[str, Any]:
    """议题列表项基础字段。"""
    return {
        "id": str(topic.id),
        "name": topic.name,
        "name_zh": topic.name_zh,
        "topic_category": topic.topic_category,
        "lifecycle_state": topic.lifecycle_state,
        "country_scope": list(topic.country_scope or []),
        "summary_zh": topic.summary_zh,
        "last_seen_at": topic.last_seen_at.isoformat() if topic.last_seen_at else None,
        "first_seen_at": topic.first_seen_at.isoformat() if topic.first_seen_at else None,
        "merged_into": str(topic.merged_into) if topic.merged_into else None,
        "naming_method": topic.naming_method,
        "cluster_method": topic.cluster_method,
        "confidence": topic.confidence,
    }


def _media_counts(db: Session, topic_ids: list[uuid.UUID]) -> dict[str, int]:
    """议题覆盖媒体数（topic_articles 关联的 articles 去重 country+domain 粗略口径：
    以 distinct article.source_id 计数，若 articles 无 source 则以 country_code 计数）。
    简化实现：按 topic_id 去重统计关联文章数，媒体数用 topic_articles 去重 source 需要
    join，直接按文章数近似（热点口径：文章越多越热）。"""
    if not topic_ids:
        return {}
    stmt = (
        select(TopicArticle.topic_id, func.count(func.distinct(TopicArticle.article_id)))
        .where(TopicArticle.topic_id.in_(topic_ids))
        .group_by(TopicArticle.topic_id)
    )
    return {str(tid): int(c) for tid, c in db.execute(stmt).all()}


@router.get("/hot")
def hot_topics(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """全局热点议题 TOP N（总览页右侧）：按 24h 全球媒体报道量降序取前 N。

    热点 = 24h 内被最多媒体/文章报道的议题（报道热度），不是显著性置信度。
    附加显著性/媒体数供卡片展示；registered 角色仅看默认 3 国。
    """
    allowed = _allowed_countries(user)

    stmt = select(Topic).where(Topic.merged_into.is_(None))
    all_topics = list(db.scalars(stmt).all())
    # registered 无 country_code 时仅看与默认 3 国有交集的议题
    if allowed is not None:
        all_topics = [
            t for t in all_topics if set(t.country_scope or []) & allowed
        ]
    topic_ids = [t.id for t in all_topics]

    snaps = _fetch_today_snapshots(db, topic_ids, None)
    article_counts = _hot_article_counts(db, topic_ids)  # 24h 报道量
    media_counts = _media_counts(db, topic_ids)
    events = _topic_event_map(db, topic_ids)

    def _agg_salience(tid: uuid.UUID) -> tuple[float, str | None]:
        candidates = [s for (t, _c), s in snaps.items() if t == str(tid)]
        if not candidates:
            return 0.0, None
        best = max(candidates, key=lambda s: float(s.salience_score or 0))
        return float(best.salience_score or 0), best.country_code

    rows: list[tuple[int, float, dict[str, Any]]] = []
    for t in all_topics:
        score, score_country = _agg_salience(t.id)
        cnt = article_counts.get(str(t.id), 0)
        item = _topic_brief(t)
        item.update({
            "salience_score": round(score, 4),
            "salience_country": score_country,
            "article_count": cnt,  # 24h 报道量（热点口径）
            "media_count": media_counts.get(str(t.id), 0),
            "has_agenda_event": str(t.id) in events,
        })
        rows.append((cnt, score, item))

    # 热点 = 24h 报道量降序；同量按显著性（可读性排序）
    rows.sort(key=lambda r: (-r[0], -r[1]))
    items = [r[2] for r in rows[:limit]]
    return ok({"items": items, "total": len(items)})


def _fetch_today_snapshots(
    db: Session, topic_ids: list[uuid.UUID], country_code: str | None,
) -> dict[tuple[str, str], AgendaSnapshot]:
    """拉取当窗（最新 window_start）每议题每国的快照，供显著性排序与字段回填。

    返回 {(topic_id_str, country_code): AgendaSnapshot}。
    """
    if not topic_ids:
        return {}
    # 取每议题每国的最新一条快照（按 window_start desc）
    stmt = (
        select(AgendaSnapshot)
        .where(AgendaSnapshot.topic_id.in_(topic_ids))
        .where(AgendaSnapshot.granularity == "hour")
    )
    if country_code:
        stmt = stmt.where(AgendaSnapshot.country_code == country_code.upper())
    rows = db.scalars(stmt).all()
    latest: dict[tuple[str, str], AgendaSnapshot] = {}
    for r in rows:
        key = (str(r.topic_id), r.country_code)
        if key not in latest or r.window_start > latest[key].window_start:
            latest[key] = r
    return latest


def _topic_event_map(db: Session, topic_ids: list[uuid.UUID]) -> dict[str, AgendaEvent]:
    """每议题当前活跃事件（latest）→ {topic_id_str: AgendaEvent}。"""
    if not topic_ids:
        return {}
    stmt = (
        select(AgendaEvent)
        .where(AgendaEvent.topic_id.in_(topic_ids))
        .order_by(AgendaEvent.created_at.desc())
    )
    out: dict[str, AgendaEvent] = {}
    for ev in db.scalars(stmt).all():
        key = str(ev.topic_id)
        if key not in out:
            out[key] = ev
    return out


def _article_counts(db: Session, topic_ids: list[uuid.UUID]) -> dict[str, int]:
    """议题总文章数（topic_articles 行数）。"""
    if not topic_ids:
        return {}
    stmt = (
        select(TopicArticle.topic_id, func.count())
        .where(TopicArticle.topic_id.in_(topic_ids))
        .group_by(TopicArticle.topic_id)
    )
    return {str(tid): int(c) for tid, c in db.execute(stmt).all()}


def _hot_article_counts(db: Session, topic_ids: list[uuid.UUID]) -> dict[str, int]:
    """议题 24h 内报道量（热点口径）：topic_articles join articles 按 published_at 过滤近 24h。"""
    if not topic_ids:
        return {}
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    stmt = (
        select(TopicArticle.topic_id, func.count(func.distinct(TopicArticle.article_id)))
        .join(Article, Article.id == TopicArticle.article_id)
        .where(
            TopicArticle.topic_id.in_(topic_ids),
            Article.published_at >= cutoff,
        )
        .group_by(TopicArticle.topic_id)
    )
    return {str(tid): int(c) for tid, c in db.execute(stmt).all()}


@router.get("")
def list_topics(
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    date_str: str | None = Query(default=None, alias="date"),
    lifecycle_state: str | None = Query(default=None),
    topic_category: str | None = Query(default=None),
    keyword: str | None = Query(default=None, max_length=100),
    sort: str = Query(default="salience"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """议题列表/排行（详细设计 1.7）。

    - registered 角色仅可访问 CN/US/JP 三国；country_code 越权 403
    - sort=salience 时按当日 agenda_snapshots.salience_score 降序（缺快照议题沉底）
    """
    if sort not in _SORTS:
        raise BizError(CODE_PARAM_INVALID, f"sort 仅支持 {sorted(_SORTS)}")
    if lifecycle_state and lifecycle_state not in _LIFECYCLE_STATES:
        raise BizError(CODE_PARAM_INVALID, f"lifecycle_state 仅支持 {sorted(_LIFECYCLE_STATES)}")
    if topic_category and topic_category not in _TOPIC_CATEGORIES:
        raise BizError(CODE_PARAM_INVALID, f"topic_category 仅支持 {sorted(_TOPIC_CATEGORIES)}")

    target_date = _validate_date(date_str) or datetime.now(UTC).date()
    cc = country_code.upper() if country_code else None
    _check_country_scope(user, cc)
    allowed = _allowed_countries(user)

    stmt = select(Topic).where(Topic.merged_into.is_(None))
    if cc:
        # country_scope JSONB 数组包含该国（简化：内存过滤以兼容 JSONB 数组类型差异）
        pass
    if lifecycle_state:
        stmt = stmt.where(Topic.lifecycle_state == lifecycle_state)
    if topic_category:
        stmt = stmt.where(Topic.topic_category == topic_category)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(Topic.name.ilike(like) | Topic.name_zh.ilike(like))
    # date 过滤：last_seen_at 在当日内（UTC）
    day_start = datetime.combine(target_date, time.min).replace(tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    stmt = stmt.where(Topic.last_seen_at >= day_start, Topic.last_seen_at < day_end)

    all_topics = list(db.scalars(stmt).all())

    # country_scope 内存过滤（JSONB 数组字段直接 IN 查询不便）
    if cc:
        all_topics = [t for t in all_topics if cc in (t.country_scope or [])]
    elif allowed is not None:
        # registered 无 country_code 时，仅看 country_scope 与默认 3 国有交集的议题
        all_topics = [
            t for t in all_topics if set(t.country_scope or []) & allowed
        ]

    topic_ids = [t.id for t in all_topics]
    snaps = _fetch_today_snapshots(db, topic_ids, cc)
    events = _topic_event_map(db, topic_ids)
    article_counts = _article_counts(db, topic_ids)

    def _agg_salience(tid: uuid.UUID) -> tuple[float, int | None, str | None]:
        """聚合该议题当日显著性：指定国取该国 score，未指定则取各国最大值。"""
        candidates = [
            s for (t, _c), s in snaps.items() if t == str(tid)
        ]
        if not candidates:
            return 0.0, None, None
        best = max(candidates, key=lambda s: float(s.salience_score or 0))
        return float(best.salience_score or 0), best.salience_rank, best.country_code

    rows: list[tuple[float, datetime, int, dict[str, Any]]] = []
    for t in all_topics:
        score, rank, score_country = _agg_salience(t.id)
        ev = events.get(str(t.id))
        item = _topic_brief(t)
        item.update({
            "salience_score": round(score, 4),
            "salience_rank": rank,
            "salience_country": score_country,
            "article_count": article_counts.get(str(t.id), 0),
            "has_agenda_event": ev is not None,
            "agenda_event_status": ev.status if ev else None,
        })
        rows.append((score, t.last_seen_at or datetime.min.replace(tzinfo=UTC), article_counts.get(str(t.id), 0), item))

    # 排序：salience（默认）/ last_seen_at / article_count
    if sort == "salience":
        rows.sort(key=lambda r: -r[0])
    elif sort == "article_count":
        rows.sort(key=lambda r: -r[2])
    else:
        rows.sort(key=lambda r: r[1], reverse=True)

    total = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    items = [r[3] for r in rows[start:end]]

    return ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    })


def _get_topic_or_404(db: Session, topic_id: uuid.UUID) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise BizError(CODE_NOT_FOUND, f"议题不存在: {topic_id}")
    return topic


@router.get("/{topic_id}")
def topic_detail(
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """议题详情（详细设计 1.7）；已 merged_into 议题 data.redirect_topic_id 表达 301 语义。"""
    topic = _get_topic_or_404(db, topic_id)
    data = _topic_brief(topic)
    data.update({
        "name_auto": topic.name_auto,
        "keywords": list(topic.keywords or []),
        "no_merge_with": [str(x) for x in (topic.no_merge_with or [])],
        "human_locked_fields": list(topic.human_locked_fields or []),
        "revision_log": list(topic.revision_log or []),
        "llm_model": topic.llm_model,
        "prompt_version": topic.prompt_version,
        "created_at": topic.created_at.isoformat() if topic.created_at else None,
        "updated_at": topic.updated_at.isoformat() if topic.updated_at else None,
    })
    # merged_from：本次议题吸收过的源议题列表
    merged_from = db.scalars(
        select(Topic.id).where(Topic.merged_into == topic.id)
    ).all()
    data["merged_from"] = [str(x) for x in merged_from]
    # 当前议题的 agenda_events 列表（简要）
    events = db.scalars(
        select(AgendaEvent).where(AgendaEvent.topic_id == topic.id)
        .order_by(AgendaEvent.created_at.desc())
    ).all()
    data["agenda_events"] = [
        {
            "id": str(ev.id),
            "status": ev.status,
            "confidence": ev.confidence,
            "origin_country_code": ev.origin_country_code,
            "origin_at": ev.origin_at.isoformat() if ev.origin_at else None,
        }
        for ev in events
    ]
    # 301 语义：已 merged_into 的源议题返回 redirect_topic_id 让前端跳转
    if topic.merged_into:
        data["redirect_topic_id"] = str(topic.merged_into)
    return ok(data)


@router.get("/{topic_id}/timeline")
def topic_timeline(
    topic_id: uuid.UUID,
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    granularity: str = Query(default="day", pattern="^(hour|day|week)$"),
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """议题时间线（详细设计 1.7 timeline）：按 (country, window) 显著性曲线。

    - granularity=hour 直接读 agenda_snapshots；day/week 由 hour 数据在 SQL 端聚合
    - registered 仅近 7 天
    """
    topic = _get_topic_or_404(db, topic_id)
    if user.role == ROLE_REGISTERED and days > 7:
        raise BizError(CODE_FORBIDDEN, "registered 仅可查看近 7 天数据")
    cc = country_code.upper() if country_code else None
    _check_country_scope(user, cc)

    now = datetime.now(UTC)
    since = now - timedelta(days=days)

    stmt = (
        select(AgendaSnapshot)
        .where(
            AgendaSnapshot.topic_id == topic.id,
            AgendaSnapshot.window_start >= since,
        )
        .order_by(AgendaSnapshot.window_start.asc())
    )
    if cc:
        stmt = stmt.where(AgendaSnapshot.country_code == cc)

    rows = list(db.scalars(stmt).all())

    # 聚合到目标粒度
    def _bucket_key(s: AgendaSnapshot) -> datetime:
        ws = s.window_start
        if granularity == "hour":
            return ws.replace(minute=0, second=0, microsecond=0)
        if granularity == "day":
            return ws.replace(hour=0, minute=0, second=0, microsecond=0)
        # week：ISO 周一起点
        monday = ws.date() - timedelta(days=ws.weekday())
        return datetime.combine(monday, time.min).replace(tzinfo=UTC)

    buckets: dict[datetime, list[AgendaSnapshot]] = {}
    for r in rows:
        buckets.setdefault(_bucket_key(r), []).append(r)

    points: list[dict[str, Any]] = []
    for ws in sorted(buckets.keys()):
        group = buckets[ws]
        article_count = sum(int(g.article_count or 0) for g in group)
        salience_score = sum(float(g.salience_score or 0) for g in group)
        best = max(group, key=lambda g: float(g.salience_score or 0))
        # top_attributes 取分数最高那条快照
        attrs = (best.top_attributes or {}).get("keywords") if isinstance(best.top_attributes, dict) else None
        points.append({
            "window_start": ws.isoformat(),
            "article_count": article_count,
            "salience_score": round(salience_score, 4),
            "salience_rank": best.salience_rank,
            "country_code": best.country_code if cc else None,
            "top_attributes": attrs or [],
        })

    return ok({
        "topic_id": str(topic.id),
        "country_code": cc,
        "granularity": granularity,
        "days": days,
        "points": points,
    })


@router.get("/{topic_id}/merge-suggestions")
def topic_merge_suggestions(
    topic_id: uuid.UUID,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """归并建议：HNSW 向量检索最近邻活跃议题，提示人工处理（详细设计 1.7）。

    不自动归并——仅返回相似度高的候选与命中 no_merge_with 的标记，供前端人工决策。
    """
    topic = _get_topic_or_404(db, topic_id)
    if topic.centroid is None:
        return ok({"topic_id": str(topic.id), "suggestions": [], "reason": "议题向量缺失"})
    distance = Topic.centroid.cosine_distance([float(v) for v in topic.centroid])
    stmt = (
        select(Topic, distance.label("dist"))
        .where(
            Topic.centroid.is_not(None),
            Topic.merged_into.is_(None),
            Topic.lifecycle_state != "archived",
            Topic.id != topic.id,
        )
        .order_by(distance)
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    no_merge_with = {str(x) for x in (topic.no_merge_with or [])}
    suggestions = []
    for cand, dist in rows:
        sim = 1.0 - float(dist)
        suggestions.append({
            "topic_id": str(cand.id),
            "name": cand.name,
            "name_zh": cand.name_zh,
            "lifecycle_state": cand.lifecycle_state,
            "similarity": round(sim, 4),
            "country_scope": list(cand.country_scope or []),
            "in_no_merge_list": str(cand.id) in no_merge_with,
        })
    return ok({"topic_id": str(topic.id), "suggestions": suggestions})


@router.put("/{topic_id}")
def rename_topic(
    topic_id: uuid.UUID,
    body: TopicRenameRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
    _license: None = Depends(require_license_active),
):
    """人工重命名/改分类（详细设计 1.7）：human_locked_fields 标记后机器不再自动推翻。"""
    if body.name is None and body.topic_category is None:
        raise BizError(CODE_PARAM_INVALID, "至少提交一个字段（name 或 topic_category）")
    topic = _get_topic_or_404(db, topic_id)
    if topic.merged_into:
        raise BizError(CODE_STATE_INVALID, "议题已 merged_into 其他议题，需先拆分")
    if topic.lifecycle_state == "archived":
        raise BizError(CODE_STATE_INVALID, "议题已 archived，不可修改")

    ip = request.client.host if request.client else None
    now = datetime.now(UTC).isoformat()
    revisions: list[dict] = list(topic.revision_log or [])
    seq = len(revisions)

    def _append(field: str, before: Any, after: Any) -> None:
        nonlocal seq
        seq += 1
        revisions.append({
            "seq": seq,
            "revised_at": now,
            "field": field,
            "before_value": before,
            "after_value": after,
            "trigger_evidence": {"type": "manual_rename"},
            "actor": "human",
            "actor_id": str(user.id),
            "model": None,
            "prompt_version": None,
            "trigger": "manual_rename",
        })

    locked = list(topic.human_locked_fields or [])
    if body.name is not None and body.name != topic.name:
        _append("name", topic.name, body.name)
        topic.name = body.name
        if "name" not in locked:
            locked.append("name")
    if body.topic_category is not None and body.topic_category != topic.topic_category:
        if body.topic_category not in _TOPIC_CATEGORIES:
            raise BizError(CODE_PARAM_INVALID, f"topic_category 仅支持 {sorted(_TOPIC_CATEGORIES)}")
        _append("topic_category", topic.topic_category, body.topic_category)
        topic.topic_category = body.topic_category
        if "topic_category" not in locked:
            locked.append("topic_category")

    topic.revision_log = revisions
    topic.human_locked_fields = locked
    db.flush()
    write_audit(
        db, "topic.rename", user=user,
        resource=f"topics/{topic_id}",
        detail={"name": body.name, "topic_category": body.topic_category},
        ip=ip,
    )
    db.commit()
    return ok({
        "id": str(topic.id),
        "name": topic.name,
        "name_auto": topic.name_auto,
        "topic_category": topic.topic_category,
        "human_locked_fields": locked,
    })


class TopicSplitRequest(BaseModel):
    """POST /topics/{parent_id}/split 请求体（详细设计 1.7）。"""

    child_topic_id: uuid.UUID = Field(description="待分裂出来的 child 议题 ID")


@router.post("/{parent_id}/split")
def split_topic_endpoint(
    parent_id: uuid.UUID,
    body: TopicSplitRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
    _license: None = Depends(require_license_active),
):
    """议题分裂/误并回滚（详细设计 1.7 + 4.2 算法 3 注释）。

    恢复 child 的 topic_id 与文章归属；双方写入 no_merge_with；
    双方 revision_log 追加 actor='human', trigger='manual_split'；
    关联 agenda_events 迁移回各自议题；写 audit_logs(action=topic.split)。
    """
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")
    try:
        parent, child = split_topic(
            db, parent_id, body.child_topic_id, actor_user_id=user.id
        )
    except SplitError as exc:
        # 校验失败：审计 failure 后向上抛，由全局异常处理器转统一响应
        write_audit(
            db, "topic.split", user=user,
            resource=f"topics/{parent_id}",
            detail={"child_topic_id": str(body.child_topic_id), "error": exc.message},
            ip=ip, user_agent=ua, result="failure",
        )
        db.commit()
        raise
    except BizError:
        raise

    write_audit(
        db, "topic.split", user=user,
        resource=f"topics/{parent_id}",
        detail={
            "child_topic_id": str(body.child_topic_id),
            "restored_topic_id": str(child.id),
            "no_merge_pair": [str(parent.id), str(child.id)],
        },
        ip=ip, user_agent=ua,
    )
    db.commit()
    return ok({
        "parent_id": str(parent.id),
        "child_id": str(child.id),
        "restored_topic_id": str(child.id),
        "no_merge_pair": [str(parent.id), str(child.id)],
    })


# 供路由层静态分析与外部复用
_ = (Numeric, cast, func, get_cache_redis)

__all__ = ["router", "CODE_NOT_FOUND", "CODE_STATE_INVALID"]
