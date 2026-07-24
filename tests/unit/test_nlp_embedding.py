"""T2.2 向量化单元测试：真实加载 mpnet 跑真实推理（模型缺失时跳过）。

跨语言语义判别是选型核心指标（ADR-005）：同事件中英报道对余弦相似度须显著高于无关报道对。
"""
import math

import pytest

from app.nlp.config import get_nlp_settings
from app.nlp.embedding import Embedder, build_embedding_text

MODEL_DIR = get_nlp_settings().embedding_model_path
pytestmark = pytest.mark.skipif(not MODEL_DIR.exists(), reason="mpnet 模型未下载（models/sentence-transformers/）")

EN_TEXT = "The central bank announced a 25 basis point rate cut to support the slowing economy."
ZH_TEXT = "央行宣布降息二十五个基点，以应对经济增长放缓的压力。"
UNRELATED = "The local football team won the championship after a dramatic final match."


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.fixture(scope="module")
def embedder():
    return Embedder()


def test_embed_dim_and_normalized(embedder):
    vectors = embedder.embed([EN_TEXT, ZH_TEXT])
    assert len(vectors) == 2
    for vec in vectors:
        assert len(vec) == 768
        norm = math.sqrt(sum(v * v for v in vec))
        assert norm == pytest.approx(1.0, abs=1e-4)  # L2 归一化，点积即 cosine


def test_crosslingual_pair_closer_than_unrelated(embedder):
    en_vec, zh_vec, other_vec = embedder.embed([EN_TEXT, ZH_TEXT, UNRELATED])
    crosslingual = _cosine(en_vec, zh_vec)
    unrelated = _cosine(en_vec, other_vec)
    assert crosslingual > 0.6  # 同事件跨语言报道对
    assert crosslingual - unrelated > 0.2  # 显著高于无关报道对


def test_empty_batch(embedder):
    assert embedder.embed([]) == []


def test_embed_article_uses_title_and_body(embedder):
    vec = embedder.embed_article("央行降息", "央行宣布降息二十五个基点。", None)
    assert len(vec) == 768


def test_build_embedding_text_truncates_body():
    text = build_embedding_text("标题", None, "长正文" * 2000)
    assert text.startswith("标题\n")
    assert len(text) < 4000  # 正文头部截断，控制 tokenize 开销
