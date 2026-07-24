"""c-TF-IDF（class-based TF-IDF）top 词计算（T2.6 可解释产出 / topics.keywords 数据源）。

与 BERTopic 同款口径：把同一簇的全部文档拼成"类文档"，
词频按类内频率 × 类间逆频率降权，抑制跨簇通用词、突出簇特征词。
自实现而非依赖 BERTopic 内部对象：Agglomerative 结果与降级回填也要用同一套。
"""
import math
from collections import Counter

from app.clustering.tokenize import tokenize


def class_tfidf_top_words(
    docs_per_class: list[list[str]],
    top_n: int,
) -> list[list[str]]:
    """输入：每类（簇）的文档文本列表；输出：每类 c-TF-IDF top_n 词（按权重降序）。"""
    if not docs_per_class:
        return []
    class_tokens: list[Counter[str]] = []
    for docs in docs_per_class:
        counter: Counter[str] = Counter()
        for text in docs:
            counter.update(tokenize(text))
        class_tokens.append(counter)

    n_classes = len(docs_per_class)
    avg_class_len = sum(sum(c.values()) for c in class_tokens) / max(n_classes, 1)
    # 词级 DF：出现在几个类中
    df: Counter[str] = Counter()
    for counter in class_tokens:
        for token in counter:
            df[token] += 1

    results: list[list[str]] = []
    for counter in class_tokens:
        total = sum(counter.values()) or 1
        scored = []
        for token, freq in counter.items():
            tf = freq / total
            # BERTopic 口径：idf = ln(avg_len / df + 1)，多簇共现词被压权
            idf = math.log(avg_class_len / max(df[token], 1) + 1)
            scored.append((token, tf * idf))
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        results.append([token for token, score in scored[:top_n] if score > 0])
    return results
