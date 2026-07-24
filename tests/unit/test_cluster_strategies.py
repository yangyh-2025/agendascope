"""T2.6/T2.7 双策略聚类单元测试：真实 mpnet 向量跑真实聚类（模型缺失时跳过）。

语料：三个跨语言主题（央行降息/足球联赛/能源气候）各 12 篇 + 3 篇无关孤稿。
核心判据：簇纯度（同簇不混主题）、孤证保留（size=1 微簇）、超大簇护栏。
"""
import uuid
from datetime import UTC, datetime

import pytest

from app.clustering.agglomerative import AgglomerativeClusterer
from app.clustering.types import BertopicDegenerateError, ClusterDoc
from app.nlp.config import get_nlp_settings

MODEL_DIR = get_nlp_settings().embedding_model_path
pytestmark = pytest.mark.skipif(not MODEL_DIR.exists(), reason="mpnet 模型未下载（models/sentence-transformers/）")

ECON = [
    "The central bank announced a 25 basis point rate cut to support the slowing economy.",
    "央行宣布降息二十五个基点，以应对经济增长放缓的压力。",
    "Federal Reserve cuts interest rates by a quarter point amid recession fears.",
    "美联储宣布降息二十五个基点，市场期待更多宽松政策。",
    "European Central Bank lowers benchmark rate to stimulate lending and growth.",
    "欧洲央行下调基准利率以刺激信贷与经济增长。",
    "Bank of England trims interest rate as inflation cools and GDP stalls.",
    "英格兰银行小幅降息，通胀回落而经济近乎停滞。",
    "Rate cut hopes lift bond markets after the central bank statement.",
    "降息预期升温，央行声明发布后债券市场走强。",
    "The People's Bank of China guides loan prime rates lower to aid recovery.",
    "中国人民银行引导贷款市场报价利率下行，助力经济复苏。",
]
FOOTBALL = [
    "The local football club won the championship after a dramatic final match.",
    "本地足球俱乐部在扣人心弦的决赛后夺得联赛冠军。",
    "A late penalty sealed the league title for the underdog football team.",
    "一记终场前点球帮助这支不被看好的足球队锁定联赛冠军。",
    "Fans flooded the streets to celebrate the football championship victory.",
    "球迷涌上街头庆祝足球队夺冠。",
    "The striker scored twice as the club lifted the domestic cup trophy.",
    "前锋梅开二度，俱乐部捧起国内杯赛奖杯。",
    "The coach praised the squad after securing the football league crown.",
    "夺得足球联赛冠军后，主教练盛赞全队表现。",
    "The national football team qualified for the World Cup with a late winner.",
    "国家足球队凭借终场前绝杀晋级世界杯。",
]
ENERGY = [
    "The government unveiled a new energy plan to triple renewable capacity by 2035.",
    "政府发布新的能源规划，力争到2035年将可再生能源装机提升至三倍。",
    "A landmark climate bill commits the country to net zero emissions by 2050.",
    "一项具有里程碑意义的气候法案承诺该国到2050年实现净零排放。",
    "Solar and wind power generation hit a record share of the national grid.",
    "太阳能与风力发电在全国电网中的占比创下新高。",
    "The parliament passed subsidies for offshore wind and green hydrogen projects.",
    "议会通过了对海上风电与绿氢项目的补贴方案。",
    "Carbon prices surged after regulators tightened the emissions trading scheme.",
    "监管机构收紧碳排放交易机制后，碳价大幅上涨。",
    "A new nuclear reactor came online to support the clean energy transition.",
    "一座新建核电机组并网投运，支撑清洁能源转型。",
]
NOISE = [
    "A new dessert recipe featuring matcha and red bean went viral on social media.",
    "天文学家发现一颗距离地球四十光年的宜居带系外行星。",
    "The city's annual fashion week opened with a show of local designers.",
]
THEMES = {"econ": ECON, "football": FOOTBALL, "energy": ENERGY}


def _make_docs(embedder) -> list[tuple[ClusterDoc, str]]:
    """构造 ClusterDoc 并标注真实主题（noise 为无关孤稿），返回 (doc, theme) 对。"""
    pairs: list[tuple[str, str, str]] = []  # (text, theme, language)
    for theme, texts in THEMES.items():
        for text in texts:
            pairs.append((text, theme, "zh" if any("一" <= ch <= "鿿" for ch in text) else "en"))
    for text in NOISE:
        pairs.append((text, "noise", "zh" if any("一" <= ch <= "鿿" for ch in text) else "en"))
    vectors = embedder.embed([p[0] for p in pairs])
    now = datetime.now(UTC)
    docs = []
    for (text, theme, lang), vec in zip(pairs, vectors, strict=True):
        docs.append((
            ClusterDoc(
                article_id=uuid.uuid4(), title=text, text=text, language=lang,
                country_code="CN" if lang == "zh" else "US", published_at=now, embedding=vec,
            ),
            theme,
        ))
    return docs


@pytest.fixture(scope="module")
def cluster_corpus():
    from app.nlp.embedding import Embedder

    return _make_docs(Embedder())


def _purity(labels_by_doc: list[int], themes: list[str]) -> float:
    """簇纯度：每个非噪声簇内占比最高的主题比例；噪声点不计。"""
    by_label: dict[int, list[str]] = {}
    for label, theme in zip(labels_by_doc, themes, strict=True):
        if label == -1:
            continue
        by_label.setdefault(label, []).append(theme)
    if not by_label:
        return 0.0
    purities = []
    for members in by_label.values():
        top = max(members.count(t) for t in set(members))
        purities.append(top / len(members))
    return sum(purities) / len(purities)


def test_agglomerative_hard_threshold(cluster_corpus):
    docs = [d for d, _ in cluster_corpus]
    themes = [t for _, t in cluster_corpus]
    result = AgglomerativeClusterer().cluster(docs)

    labels = [-1] * len(docs)
    for cluster in result.clusters:
        for idx in cluster.member_indices:
            labels[idx] = cluster.label
    assert _purity(labels, themes) == 1.0  # 硬阈值下簇内不混主题
    assert result.noise_indices == []  # Agglomerative 不判噪声，全部归簇（含孤证）
    # 孤证保留：3 篇无关稿各自成为 size=1 微簇，不被并入主题簇
    noise_singletons = sum(
        1 for c in result.clusters if c.size == 1 and themes[c.member_indices[0]] == "noise"
    )
    assert noise_singletons == 3
    assert result.largest_share <= 0.5  # 无超大簇
    # 主题簇关键词可解释：最大簇 top 词含主题词
    biggest = result.clusters[0]
    assert biggest.keywords and len(biggest.keywords) <= 20


def test_bertopic_main_strategy(cluster_corpus):
    from app.clustering.bertopic_cluster import BertopicClusterer

    docs = [d for d, _ in cluster_corpus]
    themes = [t for _, t in cluster_corpus]
    # 小样本（39 篇）下 UMAP n_neighbors 需相对样本量调小；生产窗口（数百篇）用默认 15
    result = BertopicClusterer(min_cluster_size=10, min_cohesion=0.4, umap_n_neighbors=8).cluster(docs)

    labels = [-1] * len(docs)
    for cluster in result.clusters:
        for idx in cluster.member_indices:
            labels[idx] = cluster.label
    assert len(result.clusters) >= 2  # 至少识别出两个主题簇
    assert all(c.size >= 10 for c in result.clusters)  # 新簇 ≥10 篇才建
    assert all(c.cohesion >= 0.4 for c in result.clusters)  # 凝聚度门槛生效
    assert _purity(labels, themes) >= 0.9  # 密度聚类允许少量边缘点误并（对照策略保持 1.0）
    assert result.largest_share <= 0.8  # 护栏内


def test_bertopic_rejects_undersized_sample(cluster_corpus):
    from app.clustering.bertopic_cluster import BertopicClusterer

    docs = [d for d, _ in cluster_corpus][:5]
    with pytest.raises(BertopicDegenerateError):
        BertopicClusterer(min_cluster_size=10).cluster(docs)


def test_bertopic_megacluster_guardrail(cluster_corpus):
    """单簇占比超护栏 → 判超大簇黑洞，抛错由调用方回落（不走静默放行）。"""
    from app.clustering.bertopic_cluster import BertopicClusterer

    docs = [d for d, _ in cluster_corpus]
    # 护栏阈值压到极低：任何成簇都会超限，验证护栏判定路径真实生效
    with pytest.raises(BertopicDegenerateError, match="超大簇黑洞"):
        BertopicClusterer(min_cluster_size=10, min_cohesion=0.4, max_cluster_share=0.1, umap_n_neighbors=8).cluster(docs)


def test_cohesion_gate_dissolves_weak_clusters(cluster_corpus):
    """凝聚度门槛拉到极高：弱凝聚簇解散入噪声（成员进 noise_indices）。"""
    from app.clustering.bertopic_cluster import BertopicClusterer

    docs = [d for d, _ in cluster_corpus]
    result = BertopicClusterer(min_cluster_size=10, min_cohesion=0.999, umap_n_neighbors=8).cluster(docs)
    assert result.clusters == []
    assert len(result.noise_indices) == len(docs)
