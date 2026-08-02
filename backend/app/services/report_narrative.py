"""报告叙述 LLM 生成（T4.17 增强：议题深度报告概览的分析叙述）。

议题深度报告（topic_deep）概览 section 在模板句摘要之后追加一段 LLM 生成的分析
叙述（"分析要点：…"）。数据全部来自 build_topic_deep 已算好的真实查询结果
（议题名/关键词/摘要/生命周期/置信度 + 分国快照汇总），LLM 只基于给定数据撰写，
不编造事实。

- 依赖注入 llm_annotator（复用 engine/monitor）；None 或 monitor.degraded 时
  返回 None，调用方维持现状（不含分析要点，行为与现状等价）。
- 每次判定（含失败/降级）写 llm_judgements 留痕（详细设计 3.2 不变量③），
  task_type='report_narrative'。
- 走统一 generate_structured 信号量（LLM_MAX_CONCURRENCY=2 全局限流），不另开并发。
"""
from __future__ import annotations

from typing import Any

from app.llm import prompts
from app.llm.errors import LLMError
from app.llm.schemas import ReportNarrativeOutput
from app.models.llm import LLMJudgement
from app.models.topic import Topic


def _render_by_country(by_country: dict[str, dict]) -> str:
    """把分国快照汇总渲染为 prompt 输入文本。

    by_country：{cc: {articles, best_rank, neg[], pos[]}}（build_topic_deep 口径）。
    每行 "CC: articles=X best_rank=Y neg=Z pos=W"；neg/pos 取均值，缺失显示 -。
    """
    lines: list[str] = []
    for cc, agg in sorted(by_country.items(), key=lambda kv: -kv[1]["articles"]):
        neg = f"{sum(agg['neg']) / len(agg['neg']):.2f}" if agg["neg"] else "-"
        pos = f"{sum(agg['pos']) / len(agg['pos']):.2f}" if agg["pos"] else "-"
        best_rank = agg["best_rank"] if agg["best_rank"] is not None else "-"
        lines.append(
            f"{cc}: articles={agg['articles']} best_rank={best_rank} neg={neg} pos={pos}"
        )
    return "\n".join(lines)


def generate_report_narrative(
    db: Any,
    topic: Topic,
    by_country: dict[str, dict] | None = None,
    llm_annotator: Any = None,
) -> str | None:
    """为议题深度报告概览生成一段 LLM 分析叙述。

    返回叙述文本（成功）或 None（llm_annotator 未注入/降级/LLM 失败）。
    调用方在 None 时维持现状（不含分析要点）。
    """
    if llm_annotator is None or llm_annotator.monitor.degraded:
        return None

    input_payload: dict[str, Any] = {
        "topic_name": topic.name_zh or topic.name,
        "keywords": topic.keywords or [],
        "summary": topic.summary_zh,
        "lifecycle_state": topic.lifecycle_state,
        "confidence": topic.confidence,
        "by_country": _render_by_country(by_country or {}),
    }
    template = prompts.get_prompt(prompts.TASK_REPORT_NARRATIVE)
    user_prompt = template.build_user(input_payload)
    model_name = llm_annotator.engine.model_name

    def _record(success: bool, output: dict[str, Any] | None, error: str | None, latency_s: float) -> None:
        db.add(LLMJudgement(
            topic_id=topic.id,
            task_type=prompts.TASK_REPORT_NARRATIVE,
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
            template.system, user_prompt, ReportNarrativeOutput
        )
    except LLMError as exc:
        llm_annotator.monitor.record(False, reason=str(exc)[:200])
        _record(success=False, output=None, error=str(exc)[:300], latency_s=0.0)
        return None
    llm_annotator.monitor.record(True)
    narrative = (parsed.narrative or "").strip()
    if not narrative:
        _record(success=False, output=None, error="narrative 为空", latency_s=latency_s)
        return None
    _record(success=True, output={"narrative": narrative}, error=None, latency_s=latency_s)
    return narrative


__all__ = ["generate_report_narrative"]
