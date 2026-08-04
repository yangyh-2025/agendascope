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


class MergeConfirmOutput(BaseModel):
    """LLM 议题归并语义确认输出（T3.3 增强：同日归并在向量阈值之外加 LLM 二次把关）。

    向量相似度落入可疑区（或全量策略下每对候选）时，LLM 判断两个议题簇是否描述
    **同一事件**。same_event=True 才允许归并；False 则保留独立议题。
    reasoning ≤200 字，留痕供分析师复核。
    """

    same_event: bool = Field(description="两个议题簇是否描述同一事件（允许归并）")
    confidence: str = Field(description="判定置信度：high/medium/low")
    reasoning: str = Field(
        default="",
        max_length=200,
        description="判定理由（≤200 字）：从议题名/关键词/代表性标题判断是否同一事件",
    )

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


class ReestimateConfirmOutput(BaseModel):
    """LLM 重估佐证输出（T3.13 增强：增量重估的 LLM 复核）。

    增量重估发现更早的新证据时，LLM 复核该证据是否**推翻**原首发源判定：
    - overturns_origin=True：新证据与既有议题为同一事件、时间更早、来源可靠，
      才允许推进 origin_at 修正（revision_log 附 llm_overturn 佐证）；
    - overturns_origin=False：宁可保守不推翻，维持原判定；
    - reasoning ≤200 字，留痕供分析师复核。
    """

    overturns_origin: bool = Field(description="新证据是否推翻原首发源判定")
    reasoning: str = Field(
        default="",
        max_length=200,
        description="判定理由（≤200 字）：是否同一事件/来源是否可靠/时间关系",
    )

    @field_validator("reasoning")
    @classmethod
    def _cap_reasoning(cls, value: str) -> str:
        return value.strip()[:200]


class AlertSummaryOutput(BaseModel):
    """告警理由摘要输出（T4.14 增强：预警触发时 LLM 生成中文理由摘要）。

    summary 客观陈述触发了什么条件（命中的规则条件、相关报道线索），≤200 字，
    供前端预警卡片展示与用户快速定位。
    """

    summary: str = Field(
        min_length=1,
        max_length=200,
        description="中文告警理由摘要（≤200 字）：客观陈述命中的规则条件与相关报道线索",
    )

    @field_validator("summary")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class TranslateOutput(BaseModel):
    """LLM 翻译输出（T4.19 增强：订阅日报/周报摘要用 LLM 替代 argos 离线翻译）。

    translated 为目标语言（简中）译文；保留专有名词音译、客观准确。
    """

    translated: str = Field(min_length=1, description="译文文本（简体中文）")

    @field_validator("translated")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


# ---------------------------------------------------------------------------
# 监控对象关系抽取（监控对象大改造）：从新闻正文识别实体对间的关系。
# 关键约束：
#   - relation 必须从封闭集合选择（防关系类型爆炸）
#   - evidence_quote 必须是 article.content 的原文子串（程序强校验，防幻觉）
#   - 发现新的"外围实体"时（object_is_new=True 或 subject_is_new=True），
#     需给出 new_entity_name + new_entity_type + new_entity_role，
#     且 confidence 必须为 high 才会落库（外围入库门槛）
# ---------------------------------------------------------------------------

RELATION_TYPES: tuple[str, ...] = (
    "meets",           # 会面/会谈/通话
    "sanctions",       # 制裁
    "appoints",        # 任命/提名人选
    "criticizes",      # 公开批评/谴责
    "supports",        # 公开支持/背书
    "opposes",         # 公开反对
    "allies_with",     # 结盟/战略合作
    "member_of",       # 任职/成员
    "advises",         # 顾问/咨询
    "funds",           # 资助/拨款
    "invests_in",      # 投资
    "signals_support", # 释放支持信号（间接表态）
    "travelled_to",    # 访问/出访
    "statement_about", # 发表声明谈及
    "family_of",       # 亲属关系
    "other",           # 其他（兜底）
)


class RelationItem(BaseModel):
    """单条抽取到的实体间关系。"""

    subject_name: str = Field(min_length=1, description="主体实体名（与新闻里写法一致）")
    object_name: str = Field(min_length=1, description="客体实体名")
    relation: str = Field(description="关系类型（封闭集合）")
    evidence_quote: str = Field(
        min_length=10,
        max_length=300,
        description="新闻正文中支撑此关系的原文句子（逐字摘抄，不得改写）",
    )
    confidence: str = Field(description="置信度：high/medium/low")
    subject_is_new: bool = Field(default=False, description="subject 是否为非种子新实体")
    object_is_new: bool = Field(default=False, description="object 是否为非种子新实体")
    new_entity_type: str | None = Field(
        default=None,
        description="若一端为新实体，给出其类型 person/thinktank/intl_org/gov_body",
    )
    new_entity_role: str | None = Field(
        default=None,
        description="若一端为新实体，给出其职务/角色简述",
    )

    @field_validator("relation")
    @classmethod
    def _normalize_relation(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in RELATION_TYPES:
            return "other"
        return cleaned

    @field_validator("confidence")
    @classmethod
    def _normalize_confidence(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in ("high", "medium", "low"):
            raise ValueError(f"confidence 必须为 high/medium/low: {value!r}")
        return cleaned


class RelationExtractOutput(BaseModel):
    """关系抽取输出。"""

    relations: list[RelationItem] = Field(
        default_factory=list,
        description="抽取到的关系列表；新闻只是同时提到两实体但无实际交互时返回空列表",
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
