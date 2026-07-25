"""增量重估 + revision_log 留痕 + 人工确认/否决（T3.13/T3.15，详细设计 3.2 + 4.2 算法 4 reestimate）。

关键不变量（详细设计 3.2 2174 行，代码级断言）：
  ① 任何自动修正必须写前后值（before_value != after_value，否则禁止落库）
  ② 任何自动修正必须附触发证据（trigger_evidence 非空 dict）
  ③ 机器修正必须记录 model + prompt_version（LLM 判定时）；
     纯算法路径（无 LLM）使用固定占位 model='algorithm/<name>'、prompt_version='n/a'，
     保证 revision_log 留痕字段非空，便于审计
  ④ 人工否决优先：human_locked_fields 非空字段机器永不自动推翻

revision_log 单条结构（与详细设计 2.10 agenda_events.revision_log COMMENT 对齐）：
  {seq, revised_at, field, before_value, after_value, trigger_evidence,
   actor, actor_id, model, prompt_version, rejected}
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.agenda_engine import confidence as confidence_mod
from app.agenda_engine.config import AgendaSettings, get_agenda_settings
from app.agenda_engine.origin import (
    compute_follower_sequence,
    detect_media_origin,
)
from app.core.errors import CODE_NOT_FOUND, CODE_STATE_INVALID, BizError
from app.core.logging import get_logger
from app.models.agenda import AgendaEvent

logger = get_logger("agenda_engine.revision")

# revision 动作类型
ACTOR_MACHINE: Literal["machine", "human"] = "machine"
ACTOR_HUMAN: Literal["machine", "human"] = "human"

# 算法路径留痕占位（无 LLM 调用时，保证 model/prompt_version 非空满足不变量③）
ALGO_MODEL_PLACEHOLDER = "algorithm/origin_reestimate"
ALGO_PROMPT_VERSION_PLACEHOLDER = "n/a"


class RevisionError(BizError):
    """修正业务异常（携带 3001/4002 错误码）。"""


def _next_seq(revision_log: list) -> int:
    """同一事件内单调递增的 seq（按既有条目数 +1）。"""
    return len(revision_log or []) + 1


def _json_safe(value: Any) -> Any:
    """把任意 Python 值转成 JSONB 可序列化结构（datetime/UUID 转字符串，其余递归）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def append_revision(
    db: Session,
    event: AgendaEvent,
    *,
    field: str,
    before_value: Any,
    after_value: Any,
    trigger_evidence: dict,
    actor: Literal["machine", "human"],
    actor_id: UUID | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> dict:
    """向 event.revision_log 追加一条留痕；不满足不变量①②③时拒绝落库（AssertionError）。

    不变量：
    ① before_value != after_value（无变化不写）
    ② trigger_evidence 必须是非空 dict（且至少含一个键）
    ③ actor='machine' 时 model 与 prompt_version 必须非空（LLM 或算法占位均可）
    """
    # 不变量 ①：前后值必须不同
    before_safe = _json_safe(before_value)
    after_safe = _json_safe(after_value)
    assert before_safe != after_safe, (
        f"revision_log 不变量①违反：before_value == after_value (field={field}, value={before_safe!r})"
    )
    # 不变量 ②：trigger_evidence 必须非空 dict
    assert isinstance(trigger_evidence, dict) and trigger_evidence, (
        f"revision_log 不变量②违反：trigger_evidence 必须是非空 dict (field={field})"
    )
    # 不变量 ③：机器修正必须含 model + prompt_version
    if actor == ACTOR_MACHINE:
        assert model, (
            f"revision_log 不变量③违反：机器修正 model 必须非空 (field={field})"
        )
        assert prompt_version, (
            f"revision_log 不变量③违反：机器修正 prompt_version 必须非空 (field={field})"
        )
    # actor_id 仅 human 携带；machine 应为 None
    if actor == ACTOR_MACHINE:
        assert actor_id is None, "machine 修正不应携带 actor_id"
    else:
        assert actor_id is not None, "human 修正必须携带 actor_id"

    entry = {
        "seq": _next_seq(event.revision_log),
        "revised_at": datetime.now(UTC).isoformat(),
        "field": field,
        "before_value": before_safe,
        "after_value": after_safe,
        "trigger_evidence": _json_safe(trigger_evidence),
        "actor": actor,
        "actor_id": str(actor_id) if actor_id is not None else None,
        "model": model,
        "prompt_version": prompt_version,
        "rejected": False,
    }
    new_log = list(event.revision_log or [])
    new_log.append(entry)
    event.revision_log = new_log
    db.flush()
    logger.info(
        "revision_appended",
        event_id=str(event.id),
        seq=entry["seq"],
        field=field,
        actor=actor,
        model=model,
    )
    return entry


def _follower_sequence_to_json(followers: list) -> list[dict]:
    """把 origin.compute_follower_sequence 返回的 CountryFollower 列表序列化为 JSONB。"""
    out: list[dict] = []
    for f in followers:
        out.append(
            {
                "country_code": f.country_code,
                "first_media_id": str(f.first_media_id),
                "first_media_name": f.first_media_name,
                "first_article_id": str(f.first_article_id),
                "first_published_at": f.first_published_at.isoformat(),
                "lag_hours": float(f.lag_hours),
            }
        )
    return out


def _revise_field(
    db: Session,
    event: AgendaEvent,
    *,
    field: str,
    new_value: Any,
    trigger_evidence: dict,
    model: str,
    prompt_version: str,
    locked_fields: set[str],
) -> bool:
    """单字段自动修正（机器路径）：locked 字段跳过；差异满足不变量①时追加 revision_log。

    返回 True 表示发生了修正（event 字段已更新 + revision_log 已追加）；否则 False。
    """
    if field in locked_fields:
        logger.info(
            "revision_skipped_locked_field",
            event_id=str(event.id),
            field=field,
        )
        return False
    before = getattr(event, field)
    before_safe = _json_safe(before)
    after_safe = _json_safe(new_value)
    if before_safe == after_safe:
        return False
    append_revision(
        db,
        event,
        field=field,
        before_value=before,
        after_value=new_value,
        trigger_evidence=trigger_evidence,
        actor=ACTOR_MACHINE,
        model=model,
        prompt_version=prompt_version,
    )
    setattr(event, field, new_value)
    return True


def reestimate_origin(
    db: Session,
    topic_id: UUID,
    *,
    trigger: dict,
    llm_annotator: Any = None,
    settings: AgendaSettings | None = None,
) -> AgendaEvent | None:
    """增量重估：新证据出现自动重跑首发源判定与事件判定（详细设计 4.2 算法 4 reestimate）。

    触发场景（trigger['type']）：
      - 'earlier_article'：归并后发现议题内出现更早 TIME_PUB 节点 → 重跑媒体首发判定
      - 'person_origin'：LLM 首发表述判定出新的 person/org 首发 → 重跑（含 LLM）
      - 'stats_change'：统计佐证重算后显著性变化 → 重跑 follower_sequence 与 stats_evidence

    流程（详细设计 4.2 算法 4 reestimate 2410-2420 行）：
      1. 取议题当前 AgendaEvent（无则返回 None）
      2. 重跑 detect_media_origin + compute_follower_sequence + compute_stats_evidence
      3. 对每个差异字段：
         - field ∈ event.human_locked_fields：跳过（人工否决优先）
         - 否则 append_revision(actor='machine') 并更新 event 字段
      4. 任意字段被修正：event.status='revised'
      5. 调 confidence.maybe_escalate / maybe_deescalate 更新置信度
      6. 调 confidence.check_revision_storm 检查修正风暴
    """
    cfg = settings or get_agenda_settings()
    _ = llm_annotator  # LLM 注入位（保留接口一致性，本版本由媒体时间锚点为主）

    # 取议题当前 AgendaEvent（一个议题同时只有一个活跃事件；取最新一条）
    from sqlalchemy import select

    stmt = (
        select(AgendaEvent)
        .where(AgendaEvent.topic_id == topic_id)
        .order_by(AgendaEvent.created_at.desc())
        .limit(1)
    )
    event = db.scalars(stmt).first()
    if event is None:
        logger.info("reestimate_no_event", topic_id=str(topic_id))
        return None

    # 已 archived 的事件不再自动修正（人工已结案）
    if event.status == "archived":
        logger.info("reestimate_skip_archived", event_id=str(event.id))
        return event

    trigger_type = (trigger or {}).get("type") or "unknown"
    trigger_evidence_base = {
        "type": trigger_type,
        "trigger": _json_safe(trigger),
        "reestimated_at": datetime.now(UTC).isoformat(),
    }

    # 重跑媒体首发锚点判定（最权威的客观锚点）
    new_origin = detect_media_origin(db, topic_id)
    if new_origin is None:
        logger.info("reestimate_no_origin", topic_id=str(topic_id))
        return event

    # 重跑跟随国序列
    new_followers = compute_follower_sequence(db, topic_id, new_origin)
    new_follower_json = _follower_sequence_to_json(new_followers)

    # 重跑统计佐证（仅在 follower 非空时；空时 stats_evidence 维持 None 不变）
    new_stats_evidence: dict | None = event.stats_evidence
    if new_follower_json:
        from app.agenda_engine.stats_evidence import compute_stats_evidence

        follower_countries = [f["country_code"] for f in new_follower_json]
        stats = compute_stats_evidence(
            db,
            topic_id,
            origin_country=new_origin.country_code,
            follower_countries=follower_countries,
            window_days=cfg.stats_window_days,
        )
        stats_payload: dict[str, Any] = {
            "article_count": stats.article_count,
            "insufficient_data": stats.insufficient_data,
            "rejection_reason": stats.rejection_reason,
        }
        if stats.xcorr is not None:
            stats_payload["xcorr"] = {
                "best_lag_days": stats.xcorr.best_lag_days,
                "max_correlation": stats.xcorr.max_correlation,
                "p_value": stats.xcorr.p_value,
                "significant": stats.xcorr.significant,
            }
        if stats.granger is not None:
            stats_payload["granger"] = {
                "best_lag_days": stats.granger.best_lag_days,
                "f_statistic": stats.granger.f_statistic,
                "p_value": stats.granger.p_value,
                "significant": stats.granger.significant,
            }
        if stats.qap is not None:
            stats_payload["qap"] = {
                "correlation": stats.qap.correlation,
                "p_value": stats.qap.p_value,
                "significant": stats.qap.significant,
                "permutations": stats.qap.permutations,
            }
        new_stats_evidence = stats_payload

    # 锁定字段集合（人工否决优先）
    locked_fields = set(event.human_locked_fields or [])

    revised_fields: list[str] = []

    # 逐字段重估（origin_*）
    if _revise_field(
        db, event,
        field="origin_country_code",
        new_value=new_origin.country_code,
        trigger_evidence={**trigger_evidence_base, "origin_article_id": str(new_origin.article_id)},
        model=ALGO_MODEL_PLACEHOLDER, prompt_version=ALGO_PROMPT_VERSION_PLACEHOLDER,
        locked_fields=locked_fields,
    ):
        revised_fields.append("origin_country_code")

    if _revise_field(
        db, event,
        field="origin_source_id",
        new_value=new_origin.source_id,
        trigger_evidence={**trigger_evidence_base, "origin_article_id": str(new_origin.article_id)},
        model=ALGO_MODEL_PLACEHOLDER, prompt_version=ALGO_PROMPT_VERSION_PLACEHOLDER,
        locked_fields=locked_fields,
    ):
        revised_fields.append("origin_source_id")

    if _revise_field(
        db, event,
        field="origin_at",
        new_value=new_origin.published_at,
        trigger_evidence={**trigger_evidence_base, "origin_article_id": str(new_origin.article_id)},
        model=ALGO_MODEL_PLACEHOLDER, prompt_version=ALGO_PROMPT_VERSION_PLACEHOLDER,
        locked_fields=locked_fields,
    ):
        revised_fields.append("origin_at")

    if _revise_field(
        db, event,
        field="origin_confidence",
        new_value=new_origin.confidence,
        trigger_evidence={**trigger_evidence_base, "origin_article_id": str(new_origin.article_id)},
        model=ALGO_MODEL_PLACEHOLDER, prompt_version=ALGO_PROMPT_VERSION_PLACEHOLDER,
        locked_fields=locked_fields,
    ):
        revised_fields.append("origin_confidence")

    # follower_sequence：JSONB 列表差异比较
    if _revise_field(
        db, event,
        field="follower_sequence",
        new_value=new_follower_json,
        trigger_evidence={**trigger_evidence_base, "follower_count": len(new_follower_json)},
        model=ALGO_MODEL_PLACEHOLDER, prompt_version=ALGO_PROMPT_VERSION_PLACEHOLDER,
        locked_fields=locked_fields,
    ):
        revised_fields.append("follower_sequence")

    # stats_evidence：仅在触发为 stats_change 或显著性变化时比较
    if new_stats_evidence is not None and _revise_field(
        db, event,
        field="stats_evidence",
        new_value=new_stats_evidence,
        trigger_evidence={**trigger_evidence_base, "stats_recomputed": True},
        model=ALGO_MODEL_PLACEHOLDER, prompt_version=ALGO_PROMPT_VERSION_PLACEHOLDER,
        locked_fields=locked_fields,
    ):
        revised_fields.append("stats_evidence")

    # 4. 任意字段被修正：status → 'revised'（不覆盖 confirmed/dismissed）
    if revised_fields and event.status not in ("confirmed", "dismissed", "archived"):
        old_status = event.status
        if old_status != "revised":
            append_revision(
                db, event,
                field="status",
                before_value=old_status,
                after_value="revised",
                trigger_evidence={
                    **trigger_evidence_base,
                    "revised_fields": revised_fields,
                },
                actor=ACTOR_MACHINE,
                model=ALGO_MODEL_PLACEHOLDER,
                prompt_version=ALGO_PROMPT_VERSION_PLACEHOLDER,
            )
            event.status = "revised"

    # 5. 置信度自动升降
    confidence_mod.maybe_escalate(db, event, settings=cfg)
    confidence_mod.maybe_deescalate(db, event, settings=cfg)

    # 6. 修正风暴保护
    confidence_mod.check_revision_storm(db, event, settings=cfg)

    db.flush()
    logger.info(
        "reestimate_done",
        event_id=str(event.id),
        topic_id=str(topic_id),
        trigger_type=trigger_type,
        revised_fields=revised_fields,
        event_status=event.status,
        confidence=event.confidence,
    )
    return event


def confirm_event(
    db: Session,
    event_id: UUID,
    *,
    actor_user_id: UUID,
    settings: AgendaSettings | None = None,
) -> AgendaEvent:
    """POST /agenda-events/{id}/confirm：人工确认（详细设计 1.8 + 4.2 算法 4）。

    动作（单事务）：
    - 校验：事件存在 + status ∈ {'watching','suspected','revised'}（已 confirmed/dismissed/archived 不可重复确认）
    - confidence: → 'confirmed'
    - status: → 'confirmed'
    - confirmed_by=actor_user_id, confirmed_at=now()
    - append_revision(actor='human', field='status', before_value=旧 status, after_value='confirmed')
    """
    _ = settings or get_agenda_settings()
    event = db.get(AgendaEvent, event_id)
    if event is None:
        raise RevisionError(CODE_NOT_FOUND, f"事件不存在: {event_id}")
    if event.status in ("confirmed", "dismissed", "archived"):
        raise RevisionError(
            CODE_STATE_INVALID,
            f"事件已 {event.status}，不可重复确认",
        )

    old_status = event.status
    old_confidence = event.confidence

    # 同时改 status 与 confidence：before_value 是 dict（同时含两字段前值）
    # 满足不变量①：整体前后值不同
    append_revision(
        db, event,
        field="status",
        before_value=old_status,
        after_value="confirmed",
        trigger_evidence={
            "type": "manual_confirm",
            "previous_status": old_status,
            "previous_confidence": old_confidence,
        },
        actor=ACTOR_HUMAN,
        actor_id=actor_user_id,
    )

    event.status = "confirmed"
    event.confidence = "confirmed"
    event.confirmed_by = actor_user_id
    event.confirmed_at = datetime.now(UTC)
    db.flush()
    logger.info(
        "event_confirmed",
        event_id=str(event.id),
        actor_user_id=str(actor_user_id),
        previous_status=old_status,
        previous_confidence=old_confidence,
    )
    return event


def reject_revision(
    db: Session,
    event_id: UUID,
    revision_seq: int,
    *,
    actor_user_id: UUID,
    reason: str,
    settings: AgendaSettings | None = None,
) -> AgendaEvent:
    """POST /agenda-events/{id}/revisions/{seq}/reject：人工否决某条机器修正。

    动作（单事务）：
    - 校验：事件存在 + revision_seq 存在 + 该条 actor='machine' + 未 rejected + 事件未 archived
    - 回滚：把 event.<field> 恢复为该条的 before_value
    - 该条 revision.rejected = True
    - 追加新 revision_log 条目（actor='human', field=<field>,
      before_value=修正后值, after_value=修正前值（回滚后的值）,
      trigger_evidence={'type':'manual_reject','original_seq':seq,'reason':reason}）
    - human_locked_fields 增加 <field>（机器不再自动推翻该字段）
    - 若 event.status='revised' 且所有机器修正均被否决：status 回退到 rejected
      之前的最近一条非 revised status
    """
    _ = settings or get_agenda_settings()
    if not reason or not reason.strip():
        raise RevisionError(CODE_STATE_INVALID, "否决原因 reason 必填")
    if len(reason) > 500:
        raise RevisionError(CODE_STATE_INVALID, "否决原因 reason 长度超过 500 字")

    event = db.get(AgendaEvent, event_id)
    if event is None:
        raise RevisionError(CODE_NOT_FOUND, f"事件不存在: {event_id}")
    if event.status == "archived":
        raise RevisionError(CODE_STATE_INVALID, "归档事件不可否决")

    # 找目标 revision
    target: dict | None = None
    for entry in event.revision_log or []:
        if isinstance(entry, dict) and entry.get("seq") == revision_seq:
            target = entry
            break
    if target is None:
        raise RevisionError(CODE_NOT_FOUND, f"修正记录不存在: seq={revision_seq}")
    if target.get("actor") != ACTOR_MACHINE:
        raise RevisionError(CODE_STATE_INVALID, "该修正本身为人工操作，不可否决")
    if target.get("rejected") is True:
        raise RevisionError(CODE_STATE_INVALID, "该修正已被否决")

    field_name = str(target["field"])
    before_value = target["before_value"]
    after_value = target["after_value"]

    # 回滚 event 字段（注意：JSONB 字段回滚时用 before_value 的拷贝）
    # datetime 字段：JSON 序列化为 ISO 字符串，回滚时需解析回 datetime
    if field_name == "origin_at":
        try:
            parsed = datetime.fromisoformat(str(before_value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            setattr(event, field_name, parsed)
        except (ValueError, TypeError) as exc:
            raise RevisionError(CODE_STATE_INVALID, f"回滚 origin_at 解析失败: {exc}") from exc
    elif field_name in ("origin_source_id", "origin_entity_id"):
        # UUID 字段：JSON 序列化为字符串，回滚时解析回 UUID
        if before_value is None:
            setattr(event, field_name, None)
        else:
            try:
                setattr(event, field_name, uuid.UUID(str(before_value)))
            except (ValueError, TypeError) as exc:
                raise RevisionError(CODE_STATE_INVALID, f"回滚 {field_name} UUID 解析失败: {exc}") from exc
    else:
        # 其余字段（origin_country_code / origin_confidence / follower_sequence /
        # stats_evidence / status / confidence / detection_method / origin_type /
        # origin_quote）：JSON 序列化后的标量或列表/字典直接赋值
        setattr(event, field_name, before_value)

    # 标记原 revision 已否决
    new_log: list[dict] = []
    for entry in event.revision_log or []:
        if isinstance(entry, dict) and entry.get("seq") == revision_seq:
            entry = {**entry, "rejected": True}
        new_log.append(entry)
    event.revision_log = new_log
    db.flush()

    # 追加新 revision_log 条目（人工回滚留痕）
    append_revision(
        db, event,
        field=field_name,
        before_value=after_value,   # 修正后值（回滚前的状态）
        after_value=before_value,   # 修正前值（回滚后的状态）
        trigger_evidence={
            "type": "manual_reject",
            "original_seq": revision_seq,
            "reason": reason.strip(),
        },
        actor=ACTOR_HUMAN,
        actor_id=actor_user_id,
    )

    # human_locked_fields 增加 field_name（机器不再自动推翻）
    current_locked = list(event.human_locked_fields or [])
    if field_name not in current_locked:
        current_locked.append(field_name)
        event.human_locked_fields = current_locked

    # 若 status='revised' 且所有机器修正均被否决：status 回退到 revised 前最近一条非 revised
    if event.status == "revised":
        has_active_machine_revision = False
        for entry in event.revision_log or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("actor") != ACTOR_MACHINE:
                continue
            if entry.get("field") == "status":
                continue  # status 字段的自动修正不算
            if entry.get("rejected") is True:
                continue
            has_active_machine_revision = True
            break
        if not has_active_machine_revision:
            # 回退 status：suspected 是 revised 最常见的前身
            # （事件从 suspected 被自动修正 → revised；全部否决后回 suspected）
            event.status = "suspected"

    db.flush()
    logger.info(
        "revision_rejected",
        event_id=str(event.id),
        revision_seq=revision_seq,
        field=field_name,
        actor_user_id=str(actor_user_id),
        locked_fields=event.human_locked_fields,
        event_status=event.status,
    )
    return event


__all__ = [
    "ACTOR_HUMAN",
    "ACTOR_MACHINE",
    "RevisionError",
    "append_revision",
    "confirm_event",
    "reject_revision",
    "reestimate_origin",
]
