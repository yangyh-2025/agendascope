"""prompt 模板与版本注册表（T2.13/T2.14/T2.15/T2.17/T3.8）。

每个任务类型（topic_naming/topic_category/topic_summary/first_utterance）的 prompt 带版本号，
历史版本保留在注册表中，支持换 prompt 后对历史判定批量重跑对比（T2.17）。
新增/调整 prompt 时追加新版本，禁止原地修改已发布版本。
"""
import json
from dataclasses import dataclass
from typing import Any

from app.llm.schemas import (
    AlertSummaryOutput,
    CategoryOutput,
    FinalReviewOutput,
    FirstUtteranceOutput,
    MergeConfirmOutput,
    NamingOutput,
    ReestimateConfirmOutput,
    RelationExtractOutput,
    SummaryOutput,
    TranslateOutput,
    schema_instruction,
)

TASK_NAMING = "topic_naming"
TASK_CATEGORY = "topic_category"
TASK_SUMMARY = "topic_summary"
TASK_FIRST_UTTERANCE = "first_utterance"
TASK_FINAL_REVIEW = "final_review"
TASK_MERGE_CONFIRM = "merge_confirm"
TASK_REESTIMATE_CONFIRM = "reestimate_confirm"
TASK_ALERT_SUMMARY = "alert_summary"
TASK_TRANSLATE = "translate"
TASK_RELATION_EXTRACT = "relation_extract"

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

_NAMING_SYSTEM_V2 = (
    "你是全球新闻议题监控平台的议题命名器。输入是同一议题簇内的代表性新闻标题和该簇的关键词，"
    "你要给出一个简洁、具体、可区分的议题名。\n"
    "命名要求：\n"
    "1. 6-20 个汉字，具体实体 + 事件类型，能让人一眼看懂议题是什么；\n"
    "2. **必须突出事件当事方**：在标题中体现涉及哪些国家/主体（如具体国名、机构名、人物名）；\n"
    "3. **必须体现事件的地理范围**：是单个国家国内事件、两个/多个国家间事件，还是全球/国际事件——"
    "在标题中显式点出国家或「全球」「国际」「多国」等范围词（如「俄乌停火谈判」「中美芯片管制博弈」「全球粮食价格波动」）；\n"
    "4. 不堆砌关键词，不带标点罗列，不抄袭单条标题原文，不含“新闻/报道/最新”等无信息量词；\n"
    "5. 跨语言报道归一为中文命名。\n"
    "好/坏命名对照：\n"
    "- 输入标题围绕新疆棉花被指控与多国制裁 → 好：「多国制裁新疆棉争议」；"
    "坏：「棉花、制裁、纺织业、国际贸易和外交关系综合报道」（堆砌关键词）、「新疆棉争议」（未体现多国当事方）\n"
    "- 输入标题围绕俄乌双方新一轮停火谈判 → 好：「俄乌停火谈判」；"
    "坏：「俄乌」（过泛，无法与冲突其他面向区分）\n"
    "- 输入标题围绕美联储降息预期与市场反应 → 好：「美联储降息预期发酵」；"
    "坏：「美联储宣布将基准利率维持在目标区间并表示将依据数据决定后续政策路径」（照抄标题过长）\n"
    "- 输入标题围绕多国联合军演 → 好：「美日韩联合军演」；坏：「联合军演」（未体现多国当事方）\n"
    "- 输入标题围绕全球气候峰会 → 好：「全球气候峰会达成减排共识」；坏：「气候峰会」（未体现全球性）\n"
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


# ---------------------------------------------------------------------------
# 终审审查官（T3.12，详细设计 4.2 算法 4 llm_final_review）：
# 对 suspected 议程设置事件评逻辑连贯性 1-10 分；<5 自动降疑似/驳回，≥5 维持；
# 终审不可用跳过直进人工复核队列（PRD 8.5 降级链）。
# ---------------------------------------------------------------------------
_FINAL_REVIEW_SYSTEM_V1 = (
    "你是全球新闻议题监控平台的终审审查官。给定一个由上游管线自动判定为"
    "「疑似议程设置事件」的证据包（首发源、跟随国序列、统计佐证、检测方法），"
    "你要对该判定的**逻辑连贯性**打分（1-10 分），并给出维持（completed）或"
    "驳回（rejected）结论。\n"
    "评分维度（各 0-2.5 分，总分 1-10）：\n"
    "1. 首发源是否可靠（通讯社原文 > 普通媒体 > time_source='crawled' 低置信）；\n"
    "2. 跟随链路是否合理（跟随国数 ≥3、lag_hours 序列是否符合常规传播节奏、"
    "是否存在同期独立事件更可能解释多国同期报道）；\n"
    "3. 统计佐证是否支撑（xcorr/granger 显著性、样本量是否足够、方向是否一致）；\n"
    "4. 是否存在更可能的非议程设置解释（同期重大突发事件、共享通讯社通稿、"
    "话题天然全球性——如奥运/气候变化——导致多国独立自发报道）。\n"
    "判定规则：\n"
    "- score ≥5 → verdict='completed'（维持 suspected，事件证据链进入人工复核队列）\n"
    "- score <5  → verdict='rejected'（自动降为 watching，不自动告警；样本作负例积累）\n"
    "- 仅依据给定证据包判断，不编造；reasoning ≤500 字；concerns 列出主要疑虑点。\n"
    + schema_instruction(FinalReviewOutput)
)


def _final_review_user_v1(payload: dict[str, Any]) -> str:
    followers = payload.get("follower_sequence") or []
    followers_block = (
        "\n".join(
            f"  - {f.get('country_code')}: lag={f.get('lag_hours')}h, first_media={f.get('first_media_name')}"
            for f in followers
        )
        if followers else "  （无）"
    )
    stats = payload.get("stats_evidence") or {}
    stats_block = (
        f"sample_size={stats.get('sample_size')}, "
        f"xcorr={stats.get('xcorr')}, granger={stats.get('granger')}, qap={stats.get('qap')}"
        if stats else "（无统计佐证）"
    )
    return (
        f"议题名：{payload.get('topic_name') or '（未命名）'}\n"
        f"首发类型：{payload.get('origin_type')}\n"
        f"首发国：{payload.get('origin_country_code')}\n"
        f"首发时间：{payload.get('origin_at')}\n"
        f"首发置信度：{payload.get('origin_confidence')}\n"
        f"首发引文：{payload.get('origin_quote') or '（无）'}\n"
        f"跟随国数：{payload.get('follower_count')}\n"
        f"跟随序列：\n{followers_block}\n"
        f"统计佐证：{stats_block}\n"
        f"检测方法：{payload.get('detection_method')}\n"
        "请输出符合 Schema 的 JSON。"
    )


# ---------------------------------------------------------------------------
# 议题归并语义确认（T3.3 增强）：向量阈值判定后，LLM 二次确认两簇是否同一事件。
# 解决 embedding 残余重叠带（如 suez a8=0.607 与独立洪灾 0.60-0.64 交叉）误并。
# ---------------------------------------------------------------------------
_MERGE_CONFIRM_SYSTEM_V1 = (
    "你是全球新闻议题监控平台的议题归并裁决器。给定两个议题簇（各自含议题名、"
    "关键词、代表性新闻标题），你要判断它们是否描述**同一个事件/议题**，决定是否允许归并。\n"
    "判定规则：\n"
    "1. 同一事件 = 两个簇围绕同一具体事件/议题展开（主体、事件类型、时间窗一致），"
    "跨语言报道（如中英文转载）视为同一事件；\n"
    "2. 同一议题的不同报道角度/面向 **也视为同一事件**：同一事件常有多个报道侧面"
    "（如\"AUKUS 采购核潜艇\"与\"美英澳宣布 AUKUS 安全协定\"是同一协定；\"某国洪灾灾情\"与"
    "\"某国洪灾救援进展\"是同一场洪灾），只要主体、事件、时间窗指向同一件事就允许归并；\n"
    "3. 不同事件 = 主体或具体事件**确实不同**（如\"A国央行降息\"与\"B国央行降息\"是两回事；"
    "\"某国洪灾\"与\"另一国独立洪灾\"不是同一事件）；\n"
    "4. 只有当你**高置信**确定两个簇是不同的独立事件时才给 same_event=False；"
    "若两者可能是同一事件的不同面向、或信息不足以确定，给 same_event=True（"
    "向量已确认相似，宁可不漏并）；\n"
    "5. reasoning ≤200 字，说明判断依据（主体/事件类型/时间窗/是否同一事件的不同面向）。\n"
    + schema_instruction(MergeConfirmOutput)
)


_MERGE_CONFIRM_SYSTEM_V2 = (
    "你是全球新闻议题监控平台的议题归并裁决器。给定两个议题簇（各自含议题名、"
    "关键词、代表性新闻标题），你要判断它们是否描述**同一个事件/议题**，决定是否允许归并。\n"
    "核心原则：**向量已确认两个簇高度相似（≥0.62），你只负责否决明显的误并，不负责裁决**——"
    "绝大多数情况下应给 same_event=True。\n"
    "判定规则：\n"
    "1. 同一事件 = 两个簇围绕同一具体事件/议题展开；跨语言报道（中英文转载）视为同一事件；\n"
    "2. **同一议题的不同报道角度/面向也算同一事件**：同一事件常有多个报道侧面"
    "（\"AUKUS 采购核潜艇\"与\"美英澳宣布 AUKUS 安全协定\"是同一协定；\"某国洪灾灾情\"与"
    "\"某国洪灾救援进展\"是同一场洪灾），只要主体、事件、时间窗指向同一件事就允许归并；\n"
    "3. 只有**主体或具体事件确实不同**才是不同事件（\"A国央行降息\"与\"B国央行降息\"；"
    "\"某国洪灾\"与\"另一国独立洪灾\"）；\n"
    "4. same_event=False 仅当你能**明确且高置信**说明它们是两个独立事件时给出；"
    "信息不足或可能是同一事件的不同面向时一律给 same_event=True；\n"
    "5. reasoning ≤200 字。\n"
    + schema_instruction(MergeConfirmOutput)
)


def _merge_confirm_user_v1(payload: dict[str, Any]) -> str:
    def render_side(label: str, side: dict) -> str:
        titles = side.get("titles") or []
        titles_block = "\n".join(f"    {i + 1}. {t}" for i, t in enumerate(titles)) if titles else "    （无）"
        return (
            f"{label}：\n"
            f"  议题名：{side.get('name') or '（未命名）'}\n"
            f"  关键词：{_render_keywords(side.get('keywords') or [])}\n"
            f"  代表性标题：\n{titles_block}"
        )

    return (
        "请判断以下两个议题簇是否描述同一事件。\n\n"
        f"{render_side('候选议题（将被归并）', payload.get('candidate') or {})}\n\n"
        f"{render_side('目标议题（保留）', payload.get('target') or {})}\n"
        "请输出符合 Schema 的 JSON。"
    )


# ---------------------------------------------------------------------------
# 重估 LLM 佐证（T3.13 增强）：增量重估发现更早新证据时，LLM 复核是否推翻原首发源判定。
# 仅当新证据与既有议题为同一事件、时间更早、来源可靠时才允许推进 origin_at 修正。
# ---------------------------------------------------------------------------
_REESTIMATE_CONFIRM_SYSTEM_V1 = (
    "你是全球新闻议题监控平台的重估复核官。给定一个议题当前的首发源判定依据，"
    "以及一条在增量重估中发现的「更早」新报道，你要判断这条新证据是否**推翻**"
    "原有首发源判定。\n"
    "判定规则：\n"
    "1. 仅当新报道与既有议题描述的是**同一个事件/议题**（主体、事件类型、时间窗一致），"
    "且 published_at 早于当前首发锚点、来源可靠（非转载/非抓取时间兜底）时，"
    "才判定 overturns_origin=True；\n"
    "2. 仅主题相近但事件不同（如\"A国洪灾\"与\"B国洪灾\"）、或新报道只是转载/复述、"
    "或时间并不更早，一律 overturns_origin=False——宁可保守不推翻，不冒险改判定；\n"
    "3. reasoning ≤200 字，说明是否同一事件/来源可靠性/时间关系的判断依据。\n"
    + schema_instruction(ReestimateConfirmOutput)
)


def _reestimate_confirm_user_v1(payload: dict[str, Any]) -> str:
    return (
        f"议题名：{payload.get('topic_name') or '（未命名）'}\n"
        f"当前首发类型：{payload.get('origin_type')}\n"
        f"当前首发国：{payload.get('origin_country_code')}\n"
        f"当前首发时间：{payload.get('origin_at')}\n"
        f"当前首发置信度：{payload.get('origin_confidence')}\n"
        f"当前首发引文：{payload.get('origin_quote') or '（无）'}\n"
        f"当前首发判定依据：{payload.get('origin_basis') or '（无）'}\n"
        f"新证据标题：{payload.get('new_article_title') or '（无）'}\n"
        f"新证据摘要：{payload.get('new_article_excerpt') or '（无）'}\n"
        f"新证据发布时间：{payload.get('new_article_published_at') or '（无）'}\n"
        "请判断这条新证据是否推翻原首发判定，并输出符合 Schema 的 JSON。"
    )


# ---------------------------------------------------------------------------
# 告警理由摘要（T4.14 增强）：预警规则命中触发时，LLM 生成中文理由摘要。
# 输入命中规则、匹配条件/阈值、相关报道标题（≤5 条）；客观陈述触发原因。
# ---------------------------------------------------------------------------
_ALERT_SUMMARY_SYSTEM_V1 = (
    "你是全球新闻议题监控平台的告警分析师。根据命中预警规则的规则名、匹配条件与阈值、"
    "以及相关报道标题，生成一条中文告警理由摘要，供预警卡片展示。\n"
    "要求：\n"
    "1. 客观陈述触发了什么条件（如报道量增幅超过阈值、进入该国显著性 Top N、负面占比升高），"
    "并点明涉及的国家/议题；\n"
    "2. 只依据给定信息生成，不编造规则之外的事实；\n"
    "3. 简洁 ≤200 字，一句话说清触发原因即可。\n"
    + schema_instruction(AlertSummaryOutput)
)


def _alert_summary_user_v1(payload: dict[str, Any]) -> str:
    titles = payload.get("matched_articles") or []
    titles_block = _render_titles(titles) if titles else "（无）"
    return (
        f"规则名：{payload.get('rule_name') or '（未命名）'}\n"
        f"匹配条件：{payload.get('rule_conditions') or '（无）'}\n"
        f"国家/地区：{payload.get('country_code') or '（无）'}\n"
        f"相关报道标题：\n{titles_block}\n"
        "请生成中文告警理由摘要。"
    )


# ---------------------------------------------------------------------------
# LLM 翻译（T4.19 增强）：订阅日报/周报摘要用 LLM 替代 argos 离线翻译。
# 把给定文本译成简体中文，保留专有名词音译、客观准确。
# ---------------------------------------------------------------------------
_TRANSLATE_SYSTEM_V1 = (
    "你是新闻翻译。把给定文本翻译成简体中文。\n"
    "要求：\n"
    "1. 保留专有名词（人名/地名/机构名/事件名）音译并尽量遵循中文通行译法；\n"
    "2. 客观准确，忠实原文，不增删信息、不评论；\n"
    "3. 若原文已是简体中文，直接原样输出。\n"
    + schema_instruction(TranslateOutput)
)


def _translate_user_v1(payload: dict[str, Any]) -> str:
    return f"待翻译文本：\n{payload.get('text') or ''}\n请翻译为简体中文。"


# ---------------------------------------------------------------------------
# 监控对象关系抽取（T5.1）：从新闻正文识别实体间关系（社交图谱边）。
# 强约束：
#   - 关系类型封闭集合（防类型爆炸）
#   - evidence_quote 必须逐字摘自正文（服务端校验子串，防幻觉）
#   - 允许 LLM 发现外围新实体，但只有 confidence=high 才落库
# ---------------------------------------------------------------------------
_RELATION_EXTRACT_SYSTEM_V1 = (
    "你是国际关系分析师，负责从新闻正文中识别**指定监控实体**与新闻中其他实体之间的关系。\n"
    "\n"
    "关系类型封闭集合（必须从中选择，不得自创）：\n"
    "  meets（会面/会谈/通话）\n"
    "  sanctions（制裁）\n"
    "  appoints（任命/提名）\n"
    "  criticizes（公开批评/谴责）\n"
    "  supports（公开支持/背书）\n"
    "  opposes（公开反对）\n"
    "  allies_with（结盟/战略合作）\n"
    "  member_of（任职/成员）\n"
    "  advises（顾问/咨询）\n"
    "  funds（资助/拨款）\n"
    "  invests_in（投资）\n"
    "  signals_support（释放支持信号/间接表态）\n"
    "  travelled_to（访问/出访）\n"
    "  statement_about（发表声明谈及）\n"
    "  family_of（亲属关系）\n"
    "  other（其他，兜底）\n"
    "\n"
    "判定规则：\n"
    "1. **evidence_quote 必须是新闻正文的原文子串**（逐字摘抄，不超过 300 字符，不得改写、翻译、概括）；"
    "服务端会校验是否为原文子串，改写将被丢弃。\n"
    "2. 仅当两实体间有**明确交互**（动作、表态、关系声明）才返回关系；"
    "仅同时出现（巧合同现）而无实际交互时返回空列表。\n"
    "3. subject 通常是监控实体，object 是与之交互的另一方；若新闻里客体是被动方且语义倒置，可调换。\n"
    "4. 若新闻中提及**未列入监控实体名单的重要实体**（人名/机构名），且与监控实体存在 high 置信度关系，"
    "可通过 subject_is_new=True 或 object_is_new=True 上报，并补充 new_entity_type 与 new_entity_role。"
    "但**仅 confidence=high 的新实体才会被登记入库**，medium/low 会被丢弃。\n"
    "5. 单篇新闻最多返回 5 条最强关系；不要为凑数返回弱关系。\n"
    "6. 若整篇新闻与给定监控实体无关，直接返回空 relations。\n"
    + schema_instruction(RelationExtractOutput)
)


def _relation_extract_user_v1(payload: dict[str, Any]) -> str:
    seeds = payload.get("seed_entities") or []
    seed_lines = []
    for s in seeds:
        aliases = "/".join(s.get("name_aliases") or [])
        seed_lines.append(
            f"- {s.get('name')}（{s.get('name_zh') or ''}，{s.get('role_title') or ''}，别名：{aliases}）"
        )
    seeds_block = "\n".join(seed_lines) if seed_lines else "（无）"
    title = payload.get("article_title") or ""
    content = payload.get("article_content") or ""
    # 截到 3000 字防爆 token
    if len(content) > 3000:
        content = content[:3000] + "…"
    return (
        f"【监控实体名单】\n{seeds_block}\n\n"
        f"【新闻标题】\n{title}\n\n"
        f"【新闻正文】\n{content}\n\n"
        "请识别正文中监控实体与其他实体之间的关系，输出符合 Schema 的 JSON。"
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
        "topic-naming-v2": _Template(
            task_type=TASK_NAMING, version="topic-naming-v2",
            system=_NAMING_SYSTEM_V2, user_builder=_naming_user_v1,
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
    TASK_FINAL_REVIEW: {
        "final-review-v1": _Template(
            task_type=TASK_FINAL_REVIEW, version="final-review-v1",
            system=_FINAL_REVIEW_SYSTEM_V1, user_builder=_final_review_user_v1,
        ),
    },
    TASK_MERGE_CONFIRM: {
        "merge-confirm-v1": _Template(
            task_type=TASK_MERGE_CONFIRM, version="merge-confirm-v1",
            system=_MERGE_CONFIRM_SYSTEM_V1, user_builder=_merge_confirm_user_v1,
        ),
        "merge-confirm-v2": _Template(
            task_type=TASK_MERGE_CONFIRM, version="merge-confirm-v2",
            system=_MERGE_CONFIRM_SYSTEM_V2, user_builder=_merge_confirm_user_v1,
        ),
    },
    TASK_REESTIMATE_CONFIRM: {
        "reestimate-confirm-v1": _Template(
            task_type=TASK_REESTIMATE_CONFIRM, version="reestimate-confirm-v1",
            system=_REESTIMATE_CONFIRM_SYSTEM_V1, user_builder=_reestimate_confirm_user_v1,
        ),
    },
    TASK_ALERT_SUMMARY: {
        "alert-summary-v1": _Template(
            task_type=TASK_ALERT_SUMMARY, version="alert-summary-v1",
            system=_ALERT_SUMMARY_SYSTEM_V1, user_builder=_alert_summary_user_v1,
        ),
    },
    TASK_TRANSLATE: {
        "translate-v1": _Template(
            task_type=TASK_TRANSLATE, version="translate-v1",
            system=_TRANSLATE_SYSTEM_V1, user_builder=_translate_user_v1,
        ),
    },
    TASK_RELATION_EXTRACT: {
        "relation-extract-v1": _Template(
            task_type=TASK_RELATION_EXTRACT, version="relation-extract-v1",
            system=_RELATION_EXTRACT_SYSTEM_V1, user_builder=_relation_extract_user_v1,
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
