"""议题归并 LLM 语义确认（T3.3 增强：向量阈值之外的二次把关）。

embedding 余弦阈值（merge_sim=0.62）存在残余重叠带（如 suez a8=0.607 与独立
洪灾 0.60-0.64 交叉），纯向量判定可能误并。本模块在向量命中阈值后，调 LLM 判断
两个议题簇是否描述**同一事件**，same_event=True 才允许归并。

- 依赖注入 llm_annotator（复用 engine/monitor/settings）；None 或 monitor.degraded
  时返回 None，调用方回落纯向量阈值（行为与现状等价，回放安全）。
- 每次判定（含失败/降级）写 llm_judgements 留痕（详细设计 3.2 不变量③）。
- 走统一 generate_structured 信号量（LLM_MAX_CONCURRENCY=2 全局限流），不另开并发。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.agenda_engine.config import get_agenda_settings
from app.llm import prompts
from app.llm.errors import LLMError
from app.llm.schemas import MergeConfirmOutput
from app.models.llm import LLMJudgement
from app.models.topic import Topic

MAX_TITLES = 8  # 每侧议题簇代表性标题条数（LLM 输入预算）


def confirm_same_event(
    db: Any,
    candidate: Topic,
    target: Topic,
    llm_annotator: Any,
) -> MergeConfirmOutput | None:
    """LLM 判断 candidate 与 target 是否同一事件。

    返回 MergeConfirmOutput（成功）或 None（降级/不可用/LLM 失败）。
    调用方在 None 时回落纯向量阈值决策。
    """
    if llm_annotator is None or llm_annotator.monitor.degraded:
        return None

    settings = get_agenda_settings()
    input_payload = {
        "candidate": _side_payload(db, candidate, settings),
        "target": _side_payload(db, target, settings),
    }
    template = prompts.get_prompt(prompts.TASK_MERGE_CONFIRM)
    user_prompt = template.build_user(input_payload)
    model_name = llm_annotator.engine.model_name

    def _record(success: bool, output: dict[str, Any] | None, error: str | None, latency_s: float) -> None:
        db.add(LLMJudgement(
            topic_id=candidate.id,
            task_type=prompts.TASK_MERGE_CONFIRM,
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
            template.system, user_prompt, MergeConfirmOutput
        )
    except LLMError as exc:
        llm_annotator.monitor.record(False, reason=str(exc)[:200])
        _record(success=False, output=None, error=str(exc)[:300], latency_s=0.0)
        return None
    llm_annotator.monitor.record(True)
    output_payload = {
        "same_event": parsed.same_event,
        "confidence": parsed.confidence,
        "reasoning": parsed.reasoning,
    }
    _record(success=True, output=output_payload, error=None, latency_s=latency_s)
    return parsed


def _side_payload(db: Any, topic: Topic, settings: Any) -> dict[str, Any]:
    """单侧议题簇输入：议题名 + 关键词 + 代表性标题。"""
    from app.clustering.repository import representative_titles

    return {
        "name": topic.name or topic.name_auto,
        "keywords": topic.keywords or [],
        "titles": representative_titles(db, topic.id, MAX_TITLES),
    }


def _render_confirm_evidence(parsed: MergeConfirmOutput) -> dict[str, Any]:
    """把 LLM 确认结果渲染为 revision trigger_evidence 片段。"""
    return {
        "llm_confirmed": bool(parsed.same_event),
        "confidence": parsed.confidence,
        "reasoning": parsed.reasoning,
        "at": datetime.now(UTC).isoformat(),
    }
