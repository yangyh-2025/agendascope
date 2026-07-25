"""结构化输出定义（T2.12 JSON Schema 强制）。

选型说明：采用「prompt 强约束 + JSON Schema 校验 + 解析失败重试 1 次」路线，
未引入 outlines/约束解码。理由：
1. 输出 schema 极简（单字段 JSON 对象），Qwen2.5-Instruct 系列指令遵循能力足够；
2. outlines 对 transformers 版本的耦合较紧，且在 CPU 小模型（0.5B）上收益有限；
3. 解析失败有明确兜底（重试 1 次 → 单点降级 c-TF-IDF），不静默产出脏数据。
"""
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_TOPIC_NAME_LEN = 60  # 议题名展示上限（字符），落库上限 VARCHAR(300)


class NamingOutput(BaseModel):
    """议题命名输出。"""

    name: str = Field(min_length=2, description="议题名，≤20 个汉字，具体实体+事件类型")

    @field_validator("name")
    @classmethod
    def _strip_and_cap(cls, value: str) -> str:
        cleaned = value.strip().strip("。\"'《》")
        if len(cleaned) < 2:
            raise ValueError("议题名过短")
        return cleaned[:MAX_TOPIC_NAME_LEN]


class CategoryOutput(BaseModel):
    """主题分类输出。"""

    category: str = Field(min_length=2, description="主题分类，必须属于给定分类体系")

    @field_validator("category")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip().strip("。\"'")


class SummaryOutput(BaseModel):
    """议题摘要输出。"""

    summary: str = Field(min_length=10, description="2-3 句中文摘要")

    @field_validator("summary")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class FirstUtteranceOutput(BaseModel):
    """LLM 首发表述判定输出（T3.8，详细设计 4.2 算法 4 llm_first_utterance）。

    强制输出 evidence_quote（候选片段原文摘录）作为判定依据：
    - is_first_utterance=True 时 evidence_quote 必须是候选片段中含"首次/initially/proposed"
      等首发标志的原文子串；
    - is_first_utterance=False 时 evidence_quote 可为空字符串（无依据判定）；
    - occurred_at 为 ISO 8601 时间字符串（LLM 推断首发时间；空字符串表示无法推断）；
    - reasoning ≤200 字，留痕用，供前端展示"机器为什么这样判"。
    """

    is_first_utterance: bool = Field(description="候选片段是否包含该实体对该议题的首发表述")
    evidence_quote: str = Field(
        default="",
        description="候选片段中作为判定依据的原文摘录（不得改写；无依据时为空字符串）",
    )
    confidence: str = Field(description="置信度：high/medium/low")
    occurred_at: str = Field(
        default="",
        description="首发时间（ISO 8601 字符串，LLM 推断；无法推断时为空字符串）",
    )
    reasoning: str = Field(default="", description="判定理由（≤200 字）")

    @field_validator("confidence")
    @classmethod
    def _normalize_confidence(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in ("high", "medium", "low"):
            raise ValueError(f"confidence 必须为 high/medium/low: {value!r}")
        return cleaned

    @field_validator("reasoning")
    @classmethod
    def _cap_reasoning(cls, value: str) -> str:
        return value.strip()[:200]


def schema_instruction(output_model: type[BaseModel]) -> str:
    """把 pydantic 模型的 JSON Schema 渲染为 prompt 内的强约束说明。"""
    schema = output_model.model_json_schema()
    compact = json.dumps(schema, ensure_ascii=False)
    return (
        "你必须只输出一个 JSON 对象，不要输出任何其他文字、解释或 markdown 代码块标记。\n"
        f"输出必须符合以下 JSON Schema：{compact}"
    )


class FinalReviewOutput(BaseModel):
    """LLM 终审审查官输出（T3.12，详细设计 4.2 算法 4 llm_final_review）。

    score 1-10，≥5 维持 suspected（completed）；<5 自动降为 watching（rejected）。
    reasoning ≤500 字，留痕用，供分析师复核参考。
    concerns 主要疑虑点列表（无则空数组）。
    """

    score: int = Field(ge=1, le=10, description="逻辑连贯性评分 1-10，5 分为通过阈值")
    verdict: Literal["completed", "rejected"] = Field(
        description="completed=评分≥5 维持 suspected；rejected=评分<5 自动降疑似/驳回"
    )
    reasoning: str = Field(
        max_length=500,
        description="评分理由（≤500 字）：首发源是否可靠、跟随链路是否合理、统计是否支撑、是否存在更可能的非议程设置解释",
    )
    concerns: list[str] = Field(
        default_factory=list,
        description="主要疑虑点列表（无则空数组）",
    )


def parse_structured(raw_text: str, output_model: type[BaseModel]) -> Any:
    """从模型原始输出中提取首个 JSON 对象并按 schema 校验。

    提取策略：定位第一个 '{' 与最后一个 '}' 之间的子串做 json.loads，
    容忍模型在 JSON 前后输出多余文字；校验失败抛 LLMParseError 交由上层重试/降级。
    """
    from app.llm.errors import LLMParseError

    text = raw_text.strip()
    # 去掉可能的 markdown 代码块包裹
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMParseError(f"输出中未找到 JSON 对象: {text[:120]!r}")
    candidate = text[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"JSON 解析失败: {exc}; 原文: {candidate[:120]!r}") from exc
    try:
        return output_model.model_validate(data)
    except Exception as exc:
        raise LLMParseError(f"JSON Schema 校验失败: {exc}; 数据: {candidate[:120]!r}") from exc
