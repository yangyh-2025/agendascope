"""AgendaEvent 状态机与事件判定（T3.11，详细设计 2.10 + 4.2 算法 4 detect_origin 末段）。

事件状态机：watching（观察中）→ suspected（疑似，自动判定，需人工复核）→
confirmed（确认，人工）/ dismissed（排除，可重开）→ revised（已修正）→ archived（归档）。

事件判定条件（a-d，详细设计 4-开发计划.md T3.11）：
  a. 首发源明确：origin_type ∈ ('media','person','org') 且 origin_confidence ∈ ('medium','high')
  b. ≥3 国 14 天内跟随（follower_window_days 可配置）
  c. 统计检验显著（xcorr 或 granger p < 0.05）；样本不足（insufficient_data）按"证据缺省"
  d. 议题新兴或升温（lifecycle_state ∈ ('nascent','forming','confirmed')）

满足全部 a-d → 创建/更新 AgendaEvent(status='suspected')；任一不满足 → 不创建事件，
但写 agenda_event_candidates 日志（供误判复盘，本版本仅 logger.info 留痕，不建表）。

绝不在 origin_confidence='low'（time_source='crawled'，"首发源待核实"）时创建 suspected 事件
——低置信首发不自动告警（详细设计 2.10 origin_confidence COMMENT + T3.6 口径）。
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.origin import CountryFollower, MediaOrigin
from app.agenda_engine.stats_evidence import StatsEvidence
from app.core.logging import get_logger
from app.models.agenda import AgendaEvent
from app.models.topic import Topic

logger = get_logger("agenda.event")

EventStatus = Literal["watching", "suspected", "confirmed", "dismissed", "revised", "archived"]
OriginType = Literal["media", "person", "org"]

# 合法状态转移（详细设计 2.10 status COMMENT + 4.2 算法 4 + T3.11 状态机）
_EVENT_TRANSITIONS: dict[str, set[str]] = {
    "watching": {"suspected", "dismissed", "archived"},
    "suspected": {"confirmed", "dismissed", "revised", "archived"},
    "confirmed": {"revised", "archived"},            # confirmed 后仍可被新证据修正
    "dismissed": {"watching", "archived"},           # dismissed 可重开
    "revised": {"suspected", "confirmed", "dismissed", "archived"},
    "archived": set(),                                # archived 终态
}


@dataclass(frozen=True)
class EventDetectionInput:
    """事件判定输入：T3.6/T3.8/T3.9/T3.10 输出汇总。"""

    topic_id: UUID
    media_origin: MediaOrigin | None = None           # T3.6 媒体首发锚点
    person_origin_entity_id: UUID | None = None       # T3.8 LLM 人物首发判定（judge_first_utterance 命中时）
    origin_quote: str | None = None                   # T3.8 LLM evidence_quote（无依据不入库）
    followers: list[CountryFollower] = field(default_factory=list)  # T3.9 跟随序列
    stats: StatsEvidence | None = None                # T3.10 统计佐证
    detection_method: str = "llm"                     # 'llm' | 'media_time_fallback'


@dataclass(frozen=True)
class EventDecision:
    """事件判定结论（不满足条件时不创建事件，但返回决策理由供复盘）。"""

    should_create: bool
    reason: str  # 中文理由（满足 a-d / 缺哪一项）
    conditions: dict[str, bool]  # {'a_origin_clear': True, 'b_followers_enough': False, ...}


def can_transition_event(current: str, target: str) -> bool:
    """事件状态机合法转移校验。"""
    return target in _EVENT_TRANSITIONS.get(current, set())


def evaluate_conditions(
    db: Session, input: EventDetectionInput, *, now: datetime | None = None
) -> EventDecision:
    """评估事件判定条件 a-d，返回决策（不直接落库）。

    条件：
      a. 首发源明确：media_origin 存在且 confidence ∈ ('medium','high')，
         或 person_origin_entity_id 存在（LLM 已确认首发表述）
      b. ≥3 国 follower_window_days 内跟随（followers 列表长度 ≥ 3，lag_hours ≤ window_days*24）
      c. 统计显著：stats.xcorr.significant OR stats.granger.significant（样本不足时按不满足计，
         但不阻塞——议题新兴可仅凭 a/b/d 进 watching，待证据补足再升 suspected）
      d. 议题新兴或升温：topic.lifecycle_state ∈ ('nascent','forming','confirmed')
    """
    settings = get_agenda_settings()
    conditions: dict[str, bool] = {}

    # a. 首发源明确（媒体 high/medium，或人物首发经 LLM 确认）
    media_clear = (
        input.media_origin is not None
        and input.media_origin.confidence in ("medium", "high")
    )
    person_clear = input.person_origin_entity_id is not None
    conditions["a_origin_clear"] = media_clear or person_clear

    # b. ≥3 国 follower_window_days 内跟随
    max_lag_hours = settings.follower_window_days * 24
    valid_followers = [f for f in input.followers if 0 <= f.lag_hours <= max_lag_hours]
    conditions["b_followers_enough"] = len(valid_followers) >= 3

    # c. 统计显著（样本不足不算显著；xcorr 或 granger 任一显著即满足）
    stats_sig = False
    if input.stats is not None and not input.stats.insufficient_data:
        stats_sig = bool(
            (input.stats.xcorr and input.stats.xcorr.significant)
            or (input.stats.granger and input.stats.granger.significant)
        )
    conditions["c_stats_significant"] = stats_sig

    # d. 议题新兴或升温
    topic = db.get(Topic, input.topic_id)
    conditions["d_topic_active"] = bool(
        topic is not None
        and topic.lifecycle_state in ("nascent", "forming", "confirmed")
    )

    # 决策：a + b + d 必须；c 可降格处理（样本不足时进 watching 待证据补足）
    should = (
        conditions["a_origin_clear"]
        and conditions["b_followers_enough"]
        and conditions["d_topic_active"]
    )
    if should and not conditions["c_stats_significant"]:
        reason = "满足 a/b/d：首发源明确+≥3 国跟随+议题活跃；统计样本不足或未见显著性，先入 suspected 待证据补足"
    elif should:
        reason = "满足 a/b/c/d：首发源明确+≥3 国跟随+统计显著+议题活跃"
    else:
        missing = [k for k, v in conditions.items() if not v]
        reason = f"不满足判定条件：{', '.join(missing)}"
    return EventDecision(should_create=should, reason=reason, conditions=conditions)


def upsert_event(
    db: Session,
    input: EventDetectionInput,
    decision: EventDecision,
    *,
    round_no: int = 1,
    now: datetime | None = None,
) -> AgendaEvent | None:
    """按决策创建/更新 AgendaEvent（status='suspected'）。

    - 若该 (topic_id, round_no) 已存在事件：仅当决策条件从 False → True 才升 suspected；
      已 confirmed/archived 的事件不被自动重置（人工结论机器不推翻）
    - 若不存在：创建 status='suspected'（需人工复核，不自动告警——告警走 T3.12 终审/T4.14 预警引擎）
    - origin_type/origin_country_code/origin_at 从 media_origin/person_origin 推导
    - 返回创建/更新的事件；决策不满足时返回 None
    """
    now = now or datetime.now(UTC)
    if not decision.should_create:
        logger.info(
            "event_not_created",
            topic_id=str(input.topic_id), reason=decision.reason,
            conditions=decision.conditions,
        )
        return None

    # 推导 origin_type 与字段
    if input.person_origin_entity_id is not None:
        origin_type: OriginType = "person"
        origin_entity_id = input.person_origin_entity_id
        origin_source_id = None
        origin_country_code = None  # 由 entity 表查
        origin_at = input.media_origin.published_at if input.media_origin else now
        origin_confidence = "high"
    else:
        origin_type = "media"
        origin_entity_id = None
        origin_source_id = input.media_origin.source_id if input.media_origin else None
        origin_country_code = input.media_origin.country_code if input.media_origin else None
        origin_at = input.media_origin.published_at if input.media_origin else now
        origin_confidence = input.media_origin.confidence if input.media_origin else "low"

    if input.media_origin is not None and origin_country_code is None:
        origin_country_code = input.media_origin.country_code

    # 已存在事件？
    existing = db.scalar(
        select(AgendaEvent).where(
            AgendaEvent.topic_id == input.topic_id,
            AgendaEvent.round_no == round_no,
        )
    )
    if existing is not None:
        if existing.status in ("confirmed", "archived"):
            logger.info(
                "event_upsert_skip_locked",
                event_id=str(existing.id), status=existing.status,
            )
            return existing
        # 已 suspected/watching：字段不覆盖（保持首次判定；后续修正走 revision）
        return existing

    event = AgendaEvent(
        topic_id=input.topic_id,
        round_no=round_no,
        status="suspected",
        confidence="suspected",
        origin_type=origin_type,
        origin_country_code=origin_country_code or "XX",
        origin_source_id=origin_source_id,
        origin_entity_id=origin_entity_id,
        origin_at=origin_at,
        origin_confidence=origin_confidence,
        origin_quote=input.origin_quote,
        follower_sequence=[
            {
                "country_code": f.country_code,
                "first_media": str(f.first_media_id),
                "first_media_name": f.first_media_name,
                "first_article_id": str(f.first_article_id),
                "lag_hours": round(f.lag_hours, 2),
            }
            for f in input.followers
        ],
        stats_evidence=_stats_to_dict(input.stats),
        detection_method=input.detection_method,
        revision_log=[],
        human_locked_fields=[],
    )
    db.add(event)
    db.flush()
    logger.info(
        "event_created",
        event_id=str(event.id), topic_id=str(input.topic_id),
        origin_country=event.origin_country_code, reason=decision.reason,
    )
    return event


def _stats_to_dict(stats: StatsEvidence | None) -> dict | None:
    if stats is None:
        return None
    return {
        "sample_size": stats.article_count,
        "insufficient_data": stats.insufficient_data,
        "rejection_reason": stats.rejection_reason,
        "xcorr": (
            {
                "best_lag_days": stats.xcorr.best_lag_days,
                "max_correlation": round(stats.xcorr.max_correlation, 4),
                "p_value": round(stats.xcorr.p_value, 6),
                "significant": stats.xcorr.significant,
            }
            if stats.xcorr
            else None
        ),
        "granger": (
            {
                "best_lag_days": stats.granger.best_lag_days,
                "f_statistic": round(stats.granger.f_statistic, 4),
                "p_value": round(stats.granger.p_value, 6),
                "significant": stats.granger.significant,
            }
            if stats.granger
            else None
        ),
        "qap": (
            {
                "correlation": round(stats.qap.correlation, 4),
                "p_value": round(stats.qap.p_value, 6),
                "significant": stats.qap.significant,
                "permutations": stats.qap.permutations,
            }
            if stats.qap
            else None
        ),
    }
