"""c-TF-IDF 关键词标签兜底（T2.16）。

LLM 不可用时，议题命名降级为关键词标签（naming_method=ctfidf_fallback）。
输入与 LLM 命名器一致：簇内代表标题 + 聚类引擎已算好的 c-TF-IDF top 词。

实现要点：
- 聚类引擎给出的 top 词本身即 c-TF-IDF 排序结果，直接作为高优先级候选；
- 代表标题内部再算一次 TF-IDF（每条约为一「文档」），补充 top 词不足的情况；
- 中文按 2-4 字滑窗与英文/数字按整词混合切词，避免引入额外分词依赖；
- 输出明确标注为关键词标签，不伪装成 LLM 议题名。
"""
import math
import re
from collections import Counter

# 英文/数字整词 + CJK 连续段（再切 2-4 字 n-gram）
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-']+|([一-鿿]+)")

# 中文停用虚词（兜底标签只保留实义词）
_STOPWORDS = {
    "的", "了", "在", "是", "和", "与", "对", "为", "将", "被", "把", "等", "及", "或",
    "不", "也", "都", "就", "又", "之", "其", "有", "无", "于", "以", "到", "从", "向",
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was",
    "were", "be", "been", "it", "its", "as", "at", "by", "with", "from", "that", "this",
}


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        latin, cjk = match.group(0), match.group(1)
        if cjk:
            length = len(cjk)
            if length == 1:
                continue
            # 2-4 字滑窗，长词优先（4 字成语/专名最具区分度）
            for size in (4, 3, 2):
                if length >= size:
                    tokens.extend(cjk[i : i + size] for i in range(length - size + 1))
        else:
            tokens.append(latin.lower())
    return [t for t in tokens if t not in _STOPWORDS]


def ctfidf_keywords(titles: list[str], top_words: list[str], limit: int = 5) -> list[str]:
    """融合聚类 top 词与标题 TF-IDF，产出兜底关键词序列（保序去重）。"""
    keywords: list[str] = []

    def _push(term: str) -> None:
        term = term.strip()
        if term and term not in _STOPWORDS and term not in keywords:
            keywords.append(term)

    for word in top_words:
        _push(word)

    if len(keywords) < limit and titles:
        docs = [_tokenize(t) for t in titles]
        df: Counter[str] = Counter()
        tf: Counter[str] = Counter()
        for doc in docs:
            tf.update(doc)
            df.update(set(doc))
        n_docs = max(1, len(docs))
        scored = sorted(
            tf,
            key=lambda t: tf[t] * (math.log((n_docs + 1) / (df[t] + 1)) + 1.0) * min(len(t), 4),
            reverse=True,
        )
        for term in scored:
            _push(term)
            if len(keywords) >= limit:
                break

    return keywords[:limit]


def fallback_label(titles: list[str], top_words: list[str], max_terms: int = 3) -> str:
    """生成降级关键词标签，如「关键词:新疆棉·制裁·纺织」。

    显式「关键词:」前缀避免与 LLM 议题名混淆（界面同步展示 naming_method=ctfidf_fallback）。
    """
    keywords = ctfidf_keywords(titles, top_words, limit=max_terms)
    if not keywords:
        return "关键词:（无可用关键词）"
    return "关键词:" + "·".join(keywords)
