"""预警评估引擎（T4.14，详细设计 1.10 + 3.3 时序图）。

职责：
- 15 min 周期评估 enabled 规则（与 agenda_snapshots 刷新对齐）
- 三条件（growth_rate/top_n/neg_ratio）+ AND 叠加（condition_extra）
- 防抖：同规则 1h 内已触发 → 不重复通知，仅 suppressed_count+1
- 预警风暴：单用户 1h >20 条 → 合并为摘要推送
- 事件驱动：agenda_engine 写 alert:candidates 流后立即评估该议题规则

数据源：agenda_snapshots（15 min 周期已由 snapshot_worker 维护）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerting.alert_summary import generate_alert_summary
from app.core.logging import get_logger
from app.models.agenda import AgendaEvent
from app.models.alert import Alert, AlertRule
from app.models.snapshots import TopicSnapshot as AgendaSnapshot
from app.models.topic import Topic

logger = get_logger("alerting.engine")

# 防抖窗口：同规则 1h 内已触发 → 不重复通知（详细设计 3.3）
DEBOUNCE_WINDOW = timedelta(hours=1)
# 预警风暴阈值：单用户 1h 内 >20 条触发 → 合并摘要推送
STORM_THRESHOLD = 20
STORM_WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class ConditionResult:
    """单条件评估结果。"""

    satisfied: bool
    metric: str
    value: float
    threshold: float
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleEvaluation:
    """单条规则评估结果（含 AND 叠加全部子条件）。"""

    rule_id: uuid.UUID
    triggered: bool
    matched_snapshots: list[dict[str, Any]]  # 每条 {topic_id, topic_name, country_code, ...}
    summary: dict[str, Any]


@dataclass(frozen=True)
class EngineReport:
    """单轮评估报告。"""

    evaluated_rules: int
    triggered_count: int          # 实际写入 alerts(unread) 条数
    suppressed_count: int         # 防抖合并条数（suppressed_count+1）
    storm_users: list[str]        # 触发预警风暴合并摘要的用户 id
    errors: list[str]


# ---------------------------------------------------------------------------
# 条件评估（纯函数，供单测）
# ---------------------------------------------------------------------------


def eval_growth_rate(current_count: int, baseline_count: int, threshold_pct: float) -> ConditionResult:
    """growth_rate：当前窗报道量较基线增幅 > threshold_pct（%）时满足。

    - baseline_count == 0 且 current_count > 0：视为"从无到有"=100% 增长（满足若 threshold<=100）
    - 两者均 0：不满足
    """
    if baseline_count <= 0:
        satisfied = current_count > 0 and threshold_pct <= 100.0
        value = 100.0 if current_count > 0 else 0.0
    else:
        value = (current_count - baseline_count) / baseline_count * 100.0
        satisfied = value > threshold_pct
    return ConditionResult(
        satisfied=satisfied, metric="growth_rate", value=round(value, 2),
        threshold=float(threshold_pct),
        evidence={"current_count": current_count, "baseline_count": baseline_count},
    )


def eval_top_n(salience_rank: int | None, threshold_n: float) -> ConditionResult:
    """top_n：议题在该国的 salience_rank ≤ N 时满足。"""
    if salience_rank is None:
        return ConditionResult(
            satisfied=False, metric="top_n", value=0.0, threshold=float(threshold_n),
            evidence={"salience_rank": None},
        )
    return ConditionResult(
        satisfied=int(salience_rank) <= int(threshold_n),
        metric="top_n",
        value=float(salience_rank),
        threshold=float(threshold_n),
        evidence={"salience_rank": int(salience_rank)},
    )


def eval_neg_ratio(sentiment_neg: float | None, threshold_ratio: float) -> ConditionResult:
    """neg_ratio：议题在该国的负面情感占比 > threshold 时满足。

    sentiment_neg NULL（未计算）视为不满足（不伪造数据）。
    """
    if sentiment_neg is None:
        return ConditionResult(
            satisfied=False, metric="neg_ratio", value=0.0,
            threshold=float(threshold_ratio),
            evidence={"sentiment_neg": None},
        )
    value = float(sentiment_neg)
    return ConditionResult(
        satisfied=value > float(threshold_ratio),
        metric="neg_ratio", value=value,
        threshold=float(threshold_ratio),
        evidence={"sentiment_neg": value},
    )


def combine_and(results: list[ConditionResult]) -> bool:
    """AND 叠加：所有子条件均满足才触发。"""
    return all(r.satisfied for r in results)


# ---------------------------------------------------------------------------
# 规则条件构造（从 AlertRule 行生成 condition 评估函数列表）
# ---------------------------------------------------------------------------


def parse_conditions(rule: AlertRule) -> list[tuple[str, float]]:
    """从规则行解析 (type, value) 列表：主条件 + condition_extra 叠加。

    condition_extra 契约（alert_rules API 写入为 {"and": [...]}，兼容裸 list）：
      - {"and": [{"type": "top_n", "value": 10}, ...]}  （API 标准写入格式）
      - [{"type": "top_n", "value": 10}, ...]           （裸 list，历史/直写兼容）
      - {"type": "top_n", "value": 10}                  （单条件 dict，兼容）
    返回顺序：[主条件, *叠加条件]，全部需 AND 满足。
    """
    conditions: list[tuple[str, float]] = [(rule.condition_type, float(rule.condition_value))]
    extra = rule.condition_extra
    if not extra:
        return conditions
    if isinstance(extra, dict):
        # dict 包一层 {"and": [...]} 或单条件 {"type","value"}
        items = extra.get("and") if "and" in extra else [extra]
    elif isinstance(extra, list):
        items = extra
    else:
        logger.warning(
            "alert_condition_extra_unexpected_type",
            rule_id=str(rule.id), extra_type=type(extra).__name__,
        )
        return conditions
    if not isinstance(items, list):
        items = [items]
    for item in items:
        if not isinstance(item, dict):
            continue
        ctype = item.get("type")
        cvalue = item.get("value")
        if ctype in ("growth_rate", "top_n", "neg_ratio") and cvalue is not None:
            conditions.append((ctype, float(cvalue)))
    return conditions


# ---------------------------------------------------------------------------
# 快照数据查询
# ---------------------------------------------------------------------------


def _latest_window(db: Session) -> tuple[datetime, datetime] | None:
    """取最新快照窗口（window_end 最大的一档）。"""
    row = db.execute(
        select(AgendaSnapshot.window_start, AgendaSnapshot.window_end)
        .order_by(AgendaSnapshot.window_end.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return (row[0], row[1])


def _prev_window(db: Session, current_end: datetime) -> tuple[datetime, datetime] | None:
    """取上一个快照窗口（用于 growth_rate 基线对比）。"""
    row = db.execute(
        select(AgendaSnapshot.window_start, AgendaSnapshot.window_end)
        .where(AgendaSnapshot.window_end < current_end)
        .order_by(AgendaSnapshot.window_end.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return (row[0], row[1])


def _fetch_rule_snapshots(
    db: Session, rule: AlertRule, window_start: datetime, window_end: datetime,
) -> list[AgendaSnapshot]:
    """拉规则作用域内的当窗快照（country_codes × (topic_id | keywords 匹配议题)）。"""
    stmt = select(AgendaSnapshot).where(
        AgendaSnapshot.window_start == window_start,
        AgendaSnapshot.window_end == window_end,
    )
    if rule.country_codes:
        stmt = stmt.where(AgendaSnapshot.country_code.in_(rule.country_codes))
    rows = list(db.scalars(stmt).all())
    # 议题筛选：topic_id 精确 或 keywords 命中议题 keywords/name
    if rule.topic_id:
        rows = [r for r in rows if r.topic_id == rule.topic_id]
    elif rule.keywords:
        # keywords 匹配：topic.keywords 含任一关键词 或 name 包含
        topic_ids = {
            r.topic_id for r in rows
        }
        topics = db.scalars(select(Topic).where(Topic.id.in_(topic_ids))).all() if topic_ids else []
        matched_ids: set[uuid.UUID] = set()
        for t in topics:
            t_keywords = set(t.keywords or [])
            for kw in rule.keywords:
                if kw in t_keywords or (t.name and kw in t.name) or (t.name_zh and kw in t.name_zh):
                    matched_ids.add(t.id)
                    break
        rows = [r for r in rows if r.topic_id in matched_ids]
    return rows


def _fetch_baseline_counts(
    db: Session, rule: AlertRule, prev_start: datetime, prev_end: datetime,
) -> dict[tuple[str, uuid.UUID], int]:
    """取上一窗同作用域的 article_count 作为 growth_rate 基线。

    返回 {(country_code, topic_id): article_count}。
    """
    stmt = select(
        AgendaSnapshot.country_code, AgendaSnapshot.topic_id, AgendaSnapshot.article_count,
    ).where(
        AgendaSnapshot.window_start == prev_start,
        AgendaSnapshot.window_end == prev_end,
    )
    if rule.country_codes:
        stmt = stmt.where(AgendaSnapshot.country_code.in_(rule.country_codes))
    if rule.topic_id:
        stmt = stmt.where(AgendaSnapshot.topic_id == rule.topic_id)
    rows = db.execute(stmt).all()
    return {(r[0], r[1]): int(r[2]) for r in rows}


# ---------------------------------------------------------------------------
# 单条规则评估
# ---------------------------------------------------------------------------


def evaluate_rule(
    db: Session,
    rule: AlertRule,
    now: datetime | None = None,
) -> RuleEvaluation:
    """对单条规则评估当前是否触发。

    - 拉最新快照窗
    - 按 country_codes + topic_id/keywords 过滤
    - 每个 (country, topic) 命中：三条件 AND 叠加（主+extra）评估
    - 返回 triggered=True/False + 命中明细
    """
    now = now or datetime.now(UTC)
    window = _latest_window(db)
    if window is None:
        return RuleEvaluation(rule_id=rule.id, triggered=False, matched_snapshots=[], summary={})
    window_start, window_end = window

    conditions = parse_conditions(rule)
    current_snaps = _fetch_rule_snapshots(db, rule, window_start, window_end)
    if not current_snaps:
        return RuleEvaluation(rule_id=rule.id, triggered=False, matched_snapshots=[], summary={})

    prev_window = _prev_window(db, window_end)
    baseline_map: dict[tuple[str, uuid.UUID], int] = {}
    if prev_window is not None:
        baseline_map = _fetch_baseline_counts(db, rule, prev_window[0], prev_window[1])

    matched: list[dict[str, Any]] = []
    for snap in current_snaps:
        topic = db.get(Topic, snap.topic_id)
        if topic is None:
            continue
        per_conditions: list[ConditionResult] = []
        for ctype, cvalue in conditions:
            if ctype == "growth_rate":
                baseline = baseline_map.get((snap.country_code, snap.topic_id), 0)
                per_conditions.append(eval_growth_rate(int(snap.article_count), baseline, cvalue))
            elif ctype == "top_n":
                per_conditions.append(eval_top_n(int(snap.salience_rank), cvalue))
            elif ctype == "neg_ratio":
                neg = float(snap.sentiment_neg) if snap.sentiment_neg is not None else None
                per_conditions.append(eval_neg_ratio(neg, cvalue))
        if per_conditions and combine_and(per_conditions):
            matched.append({
                "topic_id": str(snap.topic_id),
                "topic_name": topic.name_zh or topic.name,
                "country_code": snap.country_code,
                "article_count": int(snap.article_count),
                "salience_rank": int(snap.salience_rank),
                "salience_score": float(snap.salience_score),
                "sentiment_neg": float(snap.sentiment_neg) if snap.sentiment_neg is not None else None,
                "conditions": [
                    {"metric": c.metric, "value": c.value, "threshold": c.threshold}
                    for c in per_conditions
                ],
            })

    return RuleEvaluation(
        rule_id=rule.id,
        triggered=bool(matched),
        matched_snapshots=matched,
        summary={
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "evaluated_at": now.isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# 防抖 / 风暴检测
# ---------------------------------------------------------------------------


def has_recent_alert(db: Session, rule_id: uuid.UUID, since: datetime) -> Alert | None:
    """查规则自 since 起最近一条 alert（任何状态）。"""
    return db.scalar(
        select(Alert)
        .where(Alert.rule_id == rule_id, Alert.triggered_at >= since)
        .order_by(Alert.triggered_at.desc())
        .limit(1)
    )


def count_user_recent_alerts(db: Session, user_id: uuid.UUID, since: datetime) -> int:
    """统计用户近 since 时间窗内已触发 alerts 数（含 unread/read/suppressed）。"""
    return int(
        db.scalar(
            select(func.count()).select_from(Alert).where(
                Alert.user_id == user_id,
                Alert.triggered_at >= since,
            )
        ) or 0
    )


# ---------------------------------------------------------------------------
# 写入 alerts 表
# ---------------------------------------------------------------------------


def _build_alert_context(db: Session, rule: AlertRule, result: RuleEvaluation) -> dict[str, Any]:
    """构造告警理由摘要的输入上下文。

    从命中的 matched_snapshots 取议题代表性标题（跨议题最多 5 条），
    连同规则名、匹配条件/阈值、国家码一起交给 LLM 生成摘要。
    """
    from app.clustering.repository import representative_titles

    matched = result.matched_snapshots
    articles: list[str] = []
    for snap in matched:
        if len(articles) >= 5:
            break
        topic_id = snap.get("topic_id")
        if not topic_id:
            continue
        try:
            titles = representative_titles(db, uuid.UUID(str(topic_id)), limit=5)
        except (ValueError, TypeError):
            titles = []
        articles.extend(str(t) for t in titles)
    articles = articles[:5]

    conditions = []
    for snap in matched[:1]:
        for cond in snap.get("conditions") or []:
            conditions.append({
                "metric": cond.get("metric"),
                "value": cond.get("value"),
                "threshold": cond.get("threshold"),
            })

    return {
        "rule_name": rule.name,
        "rule_conditions": conditions or (rule.condition_extra or {}),
        "matched_articles": articles,
        "country_code": matched[0].get("country_code") if matched else "",
    }


def write_alert(
    db: Session,
    rule: AlertRule,
    payload: dict[str, Any],
) -> Alert:
    """写 alerts(unread)；更新 rule.last_triggered_at。"""
    alert = Alert(
        rule_id=rule.id,
        user_id=rule.user_id,
        payload=payload,
        status="unread",
    )
    db.add(alert)
    rule.last_triggered_at = datetime.now(UTC)
    db.flush()
    return alert


def write_storm_digest_alert(
    db: Session,
    user_id: uuid.UUID,
    sample_rules: list[AlertRule],
    recent_count: int,
) -> Alert | None:
    """预警风暴合并摘要：单用户 1h >20 条 → 一条摘要 alert（不重复 notify 每条）。

    挂靠 admin 系统规则（若无），直接以用户 ID 落 alerts(unread)。
    """
    from app.services.seed_service import ensure_admin
    admin = ensure_admin(db)
    rule = db.scalar(select(AlertRule).where(AlertRule.name == "系统-预警风暴摘要"))
    if rule is None:
        rule = AlertRule(
            user_id=admin.id,
            name="系统-预警风暴摘要",
            country_codes=[],
            keywords=["__alert_storm__"],
            condition_type="growth_rate",
            condition_value=0,
            notify_channels=["inapp"],
        )
        db.add(rule)
        db.flush()
    payload = {
        "kind": "alert_storm_digest",
        "message": (
            f"您最近 1 小时内触发了 {recent_count} 条预警（>{STORM_THRESHOLD} 条），"
            "系统已合并为摘要推送。建议调宽规则阈值或收窄国家/议题范围以减少噪音。"
        ),
        "recent_count": recent_count,
        "sample_rules": [{"id": str(r.id), "name": r.name} for r in sample_rules[:5]],
    }
    alert = Alert(rule_id=rule.id, user_id=user_id, payload=payload, status="unread")
    db.add(alert)
    db.flush()
    return alert


# ---------------------------------------------------------------------------
# 主入口：单轮全量评估
# ---------------------------------------------------------------------------


def run_evaluation_round(
    db: Session,
    now: datetime | None = None,
    notify_hook=None,
    only_topic_id: uuid.UUID | None = None,
    llm_annotator=None,
) -> EngineReport:
    """单轮评估：拉 enabled 规则 → 逐条评估 → 防抖 → 写 alerts → 通知 hook。

    Args:
        db: SQLAlchemy session
        now: 评估时刻（测试可注入）
        notify_hook: 可选回调，签名 (alert, rule) -> dict[str, Any]（notify_result）
        only_topic_id: 事件驱动评估时仅评估该议题相关规则
        llm_annotator: 可选 TopicAnnotator 实例；注入时告警触发会生成中文理由摘要
            （payload["summary"]），否则维持现状（向后兼容）
    """
    now = now or datetime.now(UTC)
    stmt = select(AlertRule).where(AlertRule.enabled.is_(True))
    if only_topic_id is not None:
        stmt = stmt.where(AlertRule.topic_id == only_topic_id)
    rules = list(db.scalars(stmt).all())

    evaluated = 0
    triggered = 0
    suppressed = 0
    storm_users: set[str] = set()
    errors: list[str] = []

    for rule in rules:
        evaluated += 1
        try:
            result = evaluate_rule(db, rule, now=now)
        except Exception as exc:  # noqa: BLE001 单条规则失败不阻塞其他规则
            errors.append(f"rule {rule.id}: {exc!r}"[:300])
            logger.error("alert_rule_eval_fail", rule_id=str(rule.id), error=str(exc)[:200])
            continue
        if not result.triggered:
            continue

        # 防抖：同规则 1h 已触发 → suppressed_count+1，不再写新 alert
        recent = has_recent_alert(db, rule.id, now - DEBOUNCE_WINDOW)
        if recent is not None:
            recent.suppressed_count = int(recent.suppressed_count or 0) + 1
            if recent.status == "unread":
                # 保持 unread，前端仍可看到原 alert 但 suppressed_count 增长
                pass
            suppressed += 1
            db.flush()
            continue

        # 预警风暴检测：单用户 1h 已 >20 条 → 合并摘要，跳过常规通知
        user_recent_count = count_user_recent_alerts(db, rule.user_id, now - STORM_WINDOW)
        if user_recent_count >= STORM_THRESHOLD:
            sid = str(rule.user_id)
            if sid not in storm_users:
                storm_users.add(sid)
                user_rules = [r for r in rules if r.user_id == rule.user_id]
                digest = write_storm_digest_alert(db, rule.user_id, user_rules, user_recent_count + 1)
                if notify_hook is not None and digest is not None:
                    try:
                        notify_result = notify_hook(digest, rule)
                        digest.notify_result = notify_result
                    except Exception as exc:  # noqa: BLE001
                        logger.error("alert_storm_notify_fail", error=str(exc)[:200])
            continue

        # 正常路径：写 alerts(unread) + 通知
        payload = {
            "kind": "rule_triggered",
            "rule_id": str(rule.id),
            "rule_name": rule.name,
            "matched": result.matched_snapshots,
            "summary": result.summary,
        }
        # 告警理由摘要：LLM 可用时补充中文理由摘要（payload["reason_summary"]），否则维持现状
        if llm_annotator is not None:
            try:
                alert_context = _build_alert_context(db, rule, result)
                summary_text = generate_alert_summary(db, alert_context, llm_annotator=llm_annotator)
                if summary_text:
                    payload["reason_summary"] = summary_text
            except Exception as exc:  # noqa: BLE001 摘要失败不阻塞告警落库
                logger.warning(
                    "alert_summary_fail", rule_id=str(rule.id), error=str(exc)[:200],
                )
        alert = write_alert(db, rule, payload)
        triggered += 1
        if notify_hook is not None:
            try:
                notify_result = notify_hook(alert, rule)
                alert.notify_result = notify_result
                db.flush()
            except Exception as exc:  # noqa: BLE001 通知失败不阻塞 alert 落库
                logger.error(
                    "alert_notify_fail", alert_id=str(alert.id), error=str(exc)[:200],
                )

    return EngineReport(
        evaluated_rules=evaluated,
        triggered_count=triggered,
        suppressed_count=suppressed,
        storm_users=sorted(storm_users),
        errors=errors,
    )


# ---------------------------------------------------------------------------
# 事件驱动：agenda_event suspected/confirmed → 立即评估关联议题规则
# ---------------------------------------------------------------------------


def evaluate_event_driven(
    db: Session,
    event_id: uuid.UUID,
    notify_hook=None,
    llm_annotator=None,
) -> EngineReport:
    """议程设置事件 suspected/confirmed → 立即评估该议题的规则（不等 15 min 周期）。

    议程事件本身作为 alert payload 的证据（event_id 注入 payload 便于前端跳转）。
    llm_annotator 注入时告警触发生成中文理由摘要（同 run_evaluation_round）。
    """
    event = db.get(AgendaEvent, event_id)
    if event is None:
        return EngineReport(
            evaluated_rules=0, triggered_count=0, suppressed_count=0,
            storm_users=[], errors=[f"event {event_id} not found"],
        )
    if event.status not in ("suspected", "confirmed"):
        return EngineReport(
            evaluated_rules=0, triggered_count=0, suppressed_count=0,
            storm_users=[], errors=[],
        )

    # 用基础评估 + 在 payload 中附加 event_id
    report = run_evaluation_round(
        db, notify_hook=notify_hook, only_topic_id=event.topic_id, llm_annotator=llm_annotator,
    )
    # 为已触发的 alerts 补充 event_id（在 run_evaluation_round 之外，作为补充信息）
    if report.triggered_count > 0:
        recent_alerts = db.scalars(
            select(Alert)
            .join(AlertRule, Alert.rule_id == AlertRule.id)
            .where(
                AlertRule.topic_id == event.topic_id,
                Alert.triggered_at >= datetime.now(UTC) - timedelta(minutes=1),
                Alert.status == "unread",
            )
        ).all()
        for alert in recent_alerts:
            payload = dict(alert.payload or {})
            payload["event_id"] = str(event.id)
            payload["event_status"] = event.status
            alert.payload = payload
        db.flush()
    return report


__all__ = [
    "DEBOUNCE_WINDOW",
    "STORM_THRESHOLD",
    "STORM_WINDOW",
    "ConditionResult",
    "EngineReport",
    "RuleEvaluation",
    "combine_and",
    "eval_growth_rate",
    "eval_neg_ratio",
    "eval_top_n",
    "evaluate_event_driven",
    "evaluate_rule",
    "parse_conditions",
    "run_evaluation_round",
    "write_alert",
    "write_storm_digest_alert",
]
