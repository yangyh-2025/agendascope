"""分词与 c-TF-IDF 单元测试（纯算法，无需模型/基础设施）。"""
from app.clustering.ctfidf import class_tfidf_top_words
from app.clustering.tokenize import tokenize, top_keywords


def test_tokenize_mixed_zh_en():
    tokens = tokenize("央行宣布降息 25 个基点，Federal Reserve cuts rates")
    assert "央行" in tokens
    assert "降息" in tokens
    assert "federal" in tokens and "reserve" in tokens
    # 高频虚词被过滤
    assert "的" not in tokens and "the" not in tokenize("the economy")


def test_tokenize_strips_single_char_latin():
    tokens = tokenize("a b c plan")
    assert tokens == ["plan"]


def test_ctfidf_distinguishes_classes():
    econ_docs = ["央行降息刺激经济", "美联储降息应对通胀"]
    football_docs = ["足球队夺得联赛冠军", "球迷庆祝足球夺冠"]
    econ_top, football_top = class_tfidf_top_words([econ_docs, football_docs], top_n=5)
    assert "降息" in econ_top
    assert "夺冠" in football_top
    assert "降息" not in football_top  # 类间降权：他类特征词不混入


def test_ctfidf_empty_input():
    assert class_tfidf_top_words([], top_n=5) == []


def test_top_keywords_document_frequency():
    # 同一篇重复刷词不放大权重（按文档频次统计）
    result = top_keywords(["降息 降息 降息 央行", "央行 降准"], limit=3)
    assert result[0] == "央行"  # 出现于 2 篇，DF 最高
