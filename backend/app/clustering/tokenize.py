"""多语言分词（c-TF-IDF / 关键词降级匹配共用）。

CJK 走 jieba 精确模式，拉丁文按词边界切分并小写化；过滤纯标点/单字符拉丁碎片。
不引入重型停用词表：c-TF-IDF 的类间降权天然压制高频虚词（BERTopic 同款取舍）。
"""
import re

import jieba

_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")
_LATIN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9'-]*")

# 跨语种高频虚词小表（仅去极端噪音，类间降权交给 c-TF-IDF）
_STOPWORDS = frozenset(
    ["the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "from", "as", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that", "said", "say", "says", "will", "would", "can", "could", "has", "have", "had", "not", "no", "but", "if", "then", "than", "so", "we", "you", "he", "she", "they", "them", "his", "her", "their", "our", "your", "的", "了", "和", "是", "在", "对", "与", "及", "或", "等", "将", "为", "以", "于", "就", "都", "而", "被", "把", "让", "向", "从", "到", "又", "也", "还", "其", "这", "那", "你", "我", "他", "她", "它", "我们", "你们", "他们"]
)


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def tokenize(text: str) -> list[str]:
    """中英文混合分词：jieba 处理 CJK 串，拉丁词单独抽取，合并为一个 token 流。"""
    tokens: list[str] = []
    # 先抽拉丁词并替换为空格，避免 jieba 把英文逐字母切碎
    for word in _LATIN_RE.findall(text):
        lowered = word.lower()
        if len(lowered) > 1 and lowered not in _STOPWORDS:
            tokens.append(lowered)
    cjk_text = _LATIN_RE.sub(" ", text)
    if has_cjk(cjk_text):
        for word in jieba.cut(cjk_text):
            word = word.strip()
            if len(word) > 1 and word not in _STOPWORDS and not word.isascii():
                tokens.append(word)
    return tokens


def top_keywords(texts: list[str], limit: int) -> list[str]:
    """一组文档的词频 top 词（降级匹配的议题 keywords 生成用）。"""
    freq: dict[str, int] = {}
    for text in texts:
        for token in set(tokenize(text)):  # 文档频次（DF），避免单篇长文刷词
            freq[token] = freq.get(token, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in ranked[:limit]]
