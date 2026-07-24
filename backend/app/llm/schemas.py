"""结构化输出定义（T2.12 JSON Schema 强制）。

选型说明：采用「prompt 强约束 + JSON Schema 校验 + 解析失败重试 1 次」路线，
未引入 outlines/约束解码。理由：
1. 输出 schema 极简（单字段 JSON 对象），Qwen2.5-Instruct 系列指令遵循能力足够；
2. outlines 对 transformers 版本的耦合较紧，且在 CPU 小模型（0.5B）上收益有限；
3. 解析失败有明确兜底（重试 1 次 → 单点降级 c-TF-IDF），不静默产出脏数据。
"""
import json
from typing import Any

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


def schema_instruction(output_model: type[BaseModel]) -> str:
    """把 pydantic 模型的 JSON Schema 渲染为 prompt 内的强约束说明。"""
    schema = output_model.model_json_schema()
    compact = json.dumps(schema, ensure_ascii=False)
    return (
        "你必须只输出一个 JSON 对象，不要输出任何其他文字、解释或 markdown 代码块标记。\n"
        f"输出必须符合以下 JSON Schema：{compact}"
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
