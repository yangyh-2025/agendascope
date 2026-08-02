"""增量重估 LLM 佐证（T3.13 增强：新证据是否推翻首发判定的 LLM 复核）。

reestimate_origin 在增量重估发现更早新证据时，调 LLM 复核该证据是否推翻原首发
源判定。仅当 overturns_origin=True 才推进 origin_at 等字段的机器修正
（revision_log 附 llm_overturn 佐证）。

- 依赖注入 llm_annotator（复用 engine/monitor）；None 或 monitor.degraded 时
  返回 None，调用方维持纯算法路径（行为与现状等价，回放安全）。
- 每次判定（含失败/降级）写 llm_judgements 留痕（详细设计 3.2 不变量③），
  task_type='reestimate_confirm'。
- 走统一 generate_structured 信号量（LLM_MAX_CONCURRENCY=2 全局限流），不另开并发。
"""
from __future__ import annotations

from typing import Any

from app.llm import prompts
from app.llm.errors import LLMError
from app.llm.schemas import ReestimateConfirmOutput
from app.models.agenda import AgendaEvent
from app.models.llm import LLMJudgement


def confirm_reestimate_overturn(
    db: Any,
    event: AgendaEvent,
    *,
    new_article: Any,
    llm_annotator: Any,
    topic_name: str | None = None,
) -> ReestimateConfirmOutput | None:
    """LLM 判断新证据是否推翻原首发判定。

    返回 ReestimateConfirmOutput（成功）或 None（降级/不可用/LLM 失败）。
    调用方在 None 时维持纯算法路径。
    """
    if llm_annotator is None or llm_annotator.monitor.degraded:
        return None

    origin_at = event.origin_at.isoformat() if event.origin_at else None
    # 原判定依据（revision_log 内最近一条 origin_at 的 after_value / 事件原始判定）
    origin_basis = _current_origin_basis(event)
    input_payload: dict[str, Any] = {
        "topic_name": topic_name or "",
        "origin_type": event.origin_type,
        "origin_country_code": event.origin_country_code,
        "origin_at": origin_at,
        "origin_confidence": event.origin_confidence,
        "origin_quote": event.origin_quote,
        "origin_basis": origin_basis,
        "new_article_title": (new_article.title or "") if new_article else "",
        "new_article_excerpt": _article_excerpt(new_article),
        "new_article_published_at": (
            new_article.published_at.isoformat() if getattr(new_article, "published_at", None) else None
        ),
    }
    template = prompts.get_prompt(prompts.TASK_REESTIMATE_CONFIRM)
    user_prompt = template.build_user(input_payload)
    model_name = llm_annotator.engine.model_name

    def _record(success: bool, output: dict[str, Any] | None, error: str | None, latency_s: float) -> None:
        db.add(LLMJudgement(
            topic_id=event.topic_id,
            task_type=prompts.TASK_REESTIMATE_CONFIRM,
            model_name=model_name,
            prompt_version=template.version,
            input_payload=input_payload,
            output_payload=output,
            success=success,
            naming_method=None,
            error=error,
            latency_ms=int(latency_s * 1000),
        ))
        db.flush()

    if not llm_annotator.engine.is_loaded:
        llm_annotator.engine.load()
    try:
        parsed, latency_s = llm_annotator.engine.generate_structured(
            template.system, user_prompt, ReestimateConfirmOutput
        )
    except LLMError as exc:
        llm_annotator.monitor.record(False, reason=str(exc)[:200])
        _record(success=False, output=None, error=str(exc)[:300], latency_s=0.0)
        return None
    llm_annotator.monitor.record(True)
    output_payload = {
        "overturns_origin": bool(parsed.overturns_origin),
        "reasoning": parsed.reasoning,
    }
    _record(success=True, output=output_payload, error=None, latency_s=latency_s)
    return parsed


def _article_excerpt(article: Any) -> str:
    """新证据摘录：标题 + ≤300 字正文（供 LLM 判断是否同一事件）。"""
    if article is None:
        return ""
    parts: list[str] = []
    if getattr(article, "title", None):
        parts.append(str(article.title))
    content = getattr(article, "content", None)
    if content:
        parts.append(str(content)[:300])
    return "\n".join(parts)


def _current_origin_basis(event: AgendaEvent) -> str:
    """构造当前首发判定依据文本（供 LLM 复核比对）。

    取 revision_log 内最近一条 origin_at/status 机器的说明；无则用事件原始字段。
    """
    log = event.revision_log or []
    for entry in reversed(log):
        if not isinstance(entry, dict):
            continue
        if entry.get("field") in ("origin_at", "origin_country_code") and entry.get("actor") == "machine":
            te = entry.get("trigger_evidence") or {}
            parts = [f"type={te.get('type', '')}"]
            if te.get("origin_article_id"):
                parts.append(f"origin_article_id={te['origin_article_id']}")
            return "；".join(parts)
    return "（无历史修正，原始判定）"


__all__ = ["confirm_reestimate_overturn"]
