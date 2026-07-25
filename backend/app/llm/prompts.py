"""prompt 模板与版本注册表（T2.13/T2.14/T2.15/T2.17/T3.8）。

每个任务类型（topic_naming/topic_category/topic_summary/first_utterance）的 prompt 带版本号，
历史版本保留在注册表中，支持换 prompt 后对历史判定批量重跑对比（T2.17）。
新增/调整 prompt 时追加新版本，禁止原地修改已发布版本。
"""
import json
from dataclasses import dataclass
from typing import Any

from app.llm.schemas import (
    CategoryOutput,
    FirstUtteranceOutput,
    NamingOutput,
    SummaryOutput,
    schema_instruction,
)

TASK_NAMING = "topic_naming"
TASK_CATEGORY = "topic_category"
TASK_SUMMARY = "topic_summary"
TASK_FIRST_UTTERANCE = "first_utterance"

# ---------------------------------------------------------------------------
# 主题分类体系（T2.14）：预置 7 类，部署方可经 LLM_CATEGORIES 环境变量（JSON 数组）覆盖扩展
# ---------------------------------------------------------------------------
DEFAULT_CATEGORIES: list[str] = ["政治安全", "经济金融", "军事", "科技", "能源气候", "社会民生", "其他"]

# 边界示例固化进系统提示，防分类漂移（易混边界逐条给判定规则）
CATEGORY_BOUNDARY_EXAMPLES: list[tuple[str, str, str]] = [
    ("美批准新一轮对台军售", "军事", "军售/武器/军演/冲突归军事，不归政治安全"),
    ("商务部宣布对进口芯片发起反倾销调查", "经济金融", "贸易救济/关税/市场准入归经济金融"),
    ("两国宣布制裁与反制裁措施", "政治安全", "制裁/外交博弈/选举/安全政策归政治安全"),
    ("国产光刻机实现关键技术突破", "科技", "以技术突破/研发为主体的归科技；以出口管制为主体的归政治安全"),
    ("国际油价因产油国减产大幅上涨", "能源气候", "油气/电力/新能源/碳排放归能源气候"),
    ("多地出台高校毕业生就业支持政策", "社会民生", "就业/教育/医疗/住房/人口归社会民生"),
]


@dataclass(frozen=True)
class PromptTemplate:
    """一个版本的 prompt 模板。"""

    task_type: str
    version: str
    system: str

    def build_user(self, payload: dict[str, Any]) -> str:
        raise NotImplementedError


def _render_titles(titles: list[str]) -> str:
    return "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))


def _render_keywords(top_words: list[str]) -> str:
    return "、".join(top_words) if top_words else "（无）"


def _render_boundaries() -> str:
    lines = ["分类边界示例（必须严格遵守）："]
    for title, category, rule in CATEGORY_BOUNDARY_EXAMPLES:
        lines.append(f"- 「{title}」→ {category}（{rule}）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 议题命名（T2.13）：few-shot 好/坏命名对照写进 prompt
# ---------------------------------------------------------------------------
_NAMING_SYSTEM_V1 = (
    "你是全球新闻议题监控平台的议题命名器。输入是同一议题簇内的代表性新闻标题和该簇的关键词，"
    "你要给出一个简洁、具体、可区分的议题名。\n"
    "命名要求：\n"
    "1. 6-20 个汉字，具体实体 + 事件类型，能让人一眼看懂议题是什么；\n"
    "2. 不堆砌关键词，不带标点罗列，不抄袭单条标题原文，不含“新闻/报道/最新”等无信息量词；\n"
    "3. 跨语言报道归一为中文命名。\n"
    "好/坏命名对照：\n"
    "- 输入标题围绕新疆棉花被指控与多国制裁 → 好：「新疆棉争议」；"
    "坏：「棉花、制裁、纺织业、国际贸易和外交关系综合报道」（堆砌关键词）、「议题一」（无信息量）\n"
    "- 输入标题围绕俄乌双方新一轮停火谈判 → 好：「俄乌停火谈判」；"
    "坏：「俄乌」（过泛，无法与冲突其他面向区分）\n"
    "- 输入标题围绕美联储降息预期与市场反应 → 好：「美联储降息预期发酵」；"
    "坏：「美联储宣布将基准利率维持在目标区间并表示将依据数据决定后续政策路径」（照抄标题过长）\n"
    + schema_instruction(NamingOutput)
)


def _naming_user_v1(payload: dict[str, Any]) -> str:
    return (
        "簇内代表性标题：\n"
        f"{_render_titles(payload['titles'])}\n"
        f"簇关键词（c-TF-IDF top 词）：{_render_keywords(payload['top_words'])}\n"
        "请给出议题名。"
    )


# ---------------------------------------------------------------------------
# 主题分类（T2.14）：系统提示固化边界示例防漂移
# ---------------------------------------------------------------------------
_CATEGORY_SYSTEM_V1 = (
    "你是全球新闻议题监控平台的主题分类器。把给定议题归入预置分类体系中的一类。\n"
    "分类体系：{categories}\n"
    "判定规则：\n"
    "1. 只能输出分类体系内的类别名，禁止自造类别；\n"
    "2. 以议题的主要矛盾/主体行为归类，不按涉及的次要面向归类；\n"
    "3. 确实无法归入前六类时才归「其他」。\n"
    + _render_boundaries()
    + "\n"
    + schema_instruction(CategoryOutput)
)


def _category_user_v1(payload: dict[str, Any]) -> str:
    return (
        f"议题名：{payload.get('name') or '（未命名）'}\n"
        "簇内代表性标题：\n"
        f"{_render_titles(payload['titles'])}\n"
        f"簇关键词：{_render_keywords(payload['top_words'])}\n"
        "请给出主题分类。"
    )


# ---------------------------------------------------------------------------
# 议题摘要（T2.15）：2-3 句中文摘要
# ---------------------------------------------------------------------------
_SUMMARY_SYSTEM_V1 = (
    "你是全球新闻议题监控平台的议题摘要撰写员。根据同一议题簇内的代表性标题和关键词，"
    "撰写 2-3 句中文摘要，供看板与日报引用。\n"
    "要求：\n"
    "1. 第一句说明议题是什么（主体+事件），后续句子补充最新进展或影响；\n"
    "2. 只依据给定标题与关键词，不编造标题之外的事实；\n"
    "3. 语言客观中立，不使用情绪化措辞。\n"
    + schema_instruction(SummaryOutput)
)


def _summary_user_v1(payload: dict[str, Any]) -> str:
    return (
        f"议题名：{payload.get('name') or '（未命名）'}\n"
        "簇内代表性标题：\n"
        f"{_render_titles(payload['titles'])}\n"
        f"簇关键词：{_render_keywords(payload['top_words'])}\n"
        "请撰写摘要。"
    )


# ---------------------------------------------------------------------------
# 首发表述判定（T3.8，详细设计 4.2 算法 4 llm_first_utterance）：
# 候选全文片段 + 实体历史表述摘要 ≤4000 token；强制 evidence_quote 原文摘录；
# 无依据判定返回空 quote 由调用方丢弃进人工队列。
# ---------------------------------------------------------------------------
_FIRST_UTTERANCE_SYSTEM_V1 = (
    "你是全球新闻议题监控平台的首发表述判定器。给定一篇候选报道片段、目标实体在该议题下的"
    "历史已确认首发表述摘要，以及议题背景，你要判断候选报道是否包含该实体对该议题的"
    "**首发表述**（即该实体第一次公开提出/宣布/倡议该议题相关立场、政策或行动）。\n"
    "判定规则：\n"
    "1. 仅依据给定候选片段与历史表述摘要判断，不得编造候选片段之外的事实；\n"
    "2. 若候选片段中含有「该实体主动提出的、且时间早于历史摘要中所有表述的」实质内容，"
    "则 is_first_utterance=True；\n"
    "3. 若候选片段仅引用他人更早表态、或仅转述该实体此前已公开的立场、或与该议题无关，"
    "则 is_first_utterance=False；\n"
    "4. evidence_quote 必须是候选片段的**原文摘录**（不得改写、不得翻译、不得拼接多段）；"
    "is_first_utterance=True 时 evidence_quote 必填且必须能在候选片段中作为子串找到；"
    "is_first_utterance=False 且无明确反证时 evidence_quote 填空字符串；\n"
    "5. occurred_at 给出你推断的首发时间（ISO 8601 字符串，如 2026-07-20T10:00:00+00:00），"
    "无法推断时填空字符串；\n"
    "6. reasoning 用≤200 字说明你作出判断的依据，供分析师复核。\n"
    + schema_instruction(FirstUtteranceOutput)
)


def _first_utterance_user_v1(payload: dict[str, Any]) -> str:
    history = payload.get("history_quotes") or []
    if history:
        history_lines = [
            f"  - [{record.get('occurred_at', '?')}] {record.get('quote', '')}"
            for record in history
        ]
        history_block = "实体历史已确认首发表述（按时间升序）：\n" + "\n".join(history_lines)
    else:
        history_block = "实体历史已确认首发表述：（无——该实体在该议题下尚未确认过首发）"
    titles = payload.get("topic_titles") or []
    titles_block = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(titles)) if titles else "  （无）"
    return (
        f"议题名：{payload.get('topic_name') or '（未命名）'}\n"
        f"议题代表性标题：\n{titles_block}\n"
        f"目标实体：{payload.get('entity_name') or ''}"
        f"（{payload.get('entity_type') or ''}，{payload.get('country_code') or ''}）\n"
        f"{history_block}\n"
        "候选报道片段：\n"
        f"{payload.get('candidate_excerpt') or ''}\n"
        "请判定该候选报道是否包含目标实体对该议题的首发表述，并输出符合 Schema 的 JSON。"
    )


@dataclass(frozen=True)
class _Template(PromptTemplate):
    user_builder: Any = None

    def build_user(self, payload: dict[str, Any]) -> str:
        return str(self.user_builder(payload))


# 版本注册表：task_type -> {version: template}；get_prompt 默认取最高版本
PROMPT_REGISTRY: dict[str, dict[str, PromptTemplate]] = {
    TASK_NAMING: {
        "topic-naming-v1": _Template(
            task_type=TASK_NAMING, version="topic-naming-v1",
            system=_NAMING_SYSTEM_V1, user_builder=_naming_user_v1,
        ),
    },
    TASK_CATEGORY: {
        "topic-category-v1": _Template(
            task_type=TASK_CATEGORY, version="topic-category-v1",
            system=_CATEGORY_SYSTEM_V1, user_builder=_category_user_v1,
        ),
    },
    TASK_SUMMARY: {
        "topic-summary-v1": _Template(
            task_type=TASK_SUMMARY, version="topic-summary-v1",
            system=_SUMMARY_SYSTEM_V1, user_builder=_summary_user_v1,
        ),
    },
    TASK_FIRST_UTTERANCE: {
        "first-utterance-v1": _Template(
            task_type=TASK_FIRST_UTTERANCE, version="first-utterance-v1",
            system=_FIRST_UTTERANCE_SYSTEM_V1, user_builder=_first_utterance_user_v1,
        ),
    },
}


def get_prompt(task_type: str, version: str | None = None, categories: list[str] | None = None) -> PromptTemplate:
    """取 prompt 模板；version 为空取最新（字典序最大）版本。分类任务可注入扩展后的分类体系。"""
    versions = PROMPT_REGISTRY.get(task_type)
    if not versions:
        raise KeyError(f"未知 LLM 任务类型: {task_type}")
    resolved = version or sorted(versions)[-1]
    template = versions.get(resolved)
    if template is None:
        raise KeyError(f"任务 {task_type} 无版本 {resolved}（可用: {sorted(versions)}）")
    if task_type == TASK_CATEGORY and categories:
        system = template.system.replace("{categories}", "、".join(categories))
        return _Template(
            task_type=template.task_type, version=template.version,
            system=system, user_builder=template.build_user,
        )
    return template


def parse_categories(raw: str) -> list[str] | None:
    """解析 LLM_CATEGORIES 配置（JSON 数组）；非法配置直接报错，不静默吞掉。"""
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, list) or not all(isinstance(c, str) and c.strip() for c in data):
        raise ValueError("LLM_CATEGORIES 必须是非空字符串组成的 JSON 数组")
    return [c.strip() for c in data]
