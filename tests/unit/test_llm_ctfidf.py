"""c-TF-IDF 兜底关键词与标签单元测试（T2.16）。"""
from app.llm.ctfidf import ctfidf_keywords, fallback_label

TITLES_CN = [
    "俄乌双方就停火协议展开新一轮谈判",
    "俄乌谈判在伊斯坦布尔重启 停火成焦点",
    "乌克兰与俄罗斯代表就停火条件交换意见",
    "多方斡旋推动俄乌停火谈判取得进展",
]


def test_ctfidf_keywords_prefers_provided_top_words():
    keywords = ctfidf_keywords(TITLES_CN, ["停火", "斡旋"], limit=5)
    assert keywords[0] == "停火"
    assert keywords[1] == "斡旋"
    assert len(keywords) <= 5


def test_ctfidf_keywords_extracts_from_titles_when_no_top_words():
    keywords = ctfidf_keywords(TITLES_CN, [], limit=5)
    assert keywords, "应从代表标题中提取关键词"
    # 高频实义词应被提出（如「停火」「谈判」出现在多条标题中）
    joined = "".join(keywords)
    assert "停火" in joined or "谈判" in joined


def test_ctfidf_keywords_english_titles():
    titles = [
        "Federal Reserve signals rate cut as inflation cools",
        "Fed rate cut expectations lift stock markets",
        "Markets rally on Federal Reserve dovish signal",
    ]
    keywords = ctfidf_keywords(titles, [], limit=5)
    lowered = {k.lower() for k in keywords}
    assert any(k in lowered for k in ("fed", "federal", "rate"))


def test_ctfidf_keywords_dedup_and_limit():
    keywords = ctfidf_keywords(TITLES_CN, ["停火", "停火", "谈判"], limit=2)
    assert len(keywords) == 2
    assert len(set(keywords)) == len(keywords)


def test_fallback_label_format_and_no_masquerade():
    label = fallback_label(TITLES_CN, ["停火", "谈判"])
    assert label.startswith("关键词:"), "兜底标签必须显式标注为关键词标签，不伪装 LLM 议题名"
    assert "停火" in label


def test_fallback_label_empty_inputs():
    assert fallback_label([], []) == "关键词:（无可用关键词）"
