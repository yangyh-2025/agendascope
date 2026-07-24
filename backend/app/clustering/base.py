"""策略层公共组装：标签数组 → StrategyResult（质心/凝聚度/c-TF-IDF/护栏指标）。"""
import numpy as np

from app.clustering.ctfidf import class_tfidf_top_words
from app.clustering.types import NOISE_LABEL, ClusterDoc, ClusterInfo, StrategyResult


def _centroid_and_cohesion(vectors: np.ndarray) -> tuple[list[float], float]:
    """簇质心（L2 归一化均值向量）与凝聚度（成员对质心平均 cosine）。"""
    centroid = vectors.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    # 输入向量已由 Embedder L2 归一化，点积即 cosine
    cohesion = float(np.mean(vectors @ centroid)) if len(vectors) else 0.0
    return [float(v) for v in centroid], cohesion


def build_result(
    method: str,
    labels: list[int],
    docs: list[ClusterDoc],
    ctfidf_top_n: int,
    duration_ms: float,
) -> StrategyResult:
    """把聚类标签组装为 StrategyResult；噪声（-1）单列，孤证簇照常保留。"""
    embeddings = np.asarray([d.embedding for d in docs], dtype=np.float64)
    by_label: dict[int, list[int]] = {}
    noise: list[int] = []
    for idx, label in enumerate(labels):
        if label == NOISE_LABEL:
            noise.append(idx)
        else:
            by_label.setdefault(label, []).append(idx)

    clusters: list[ClusterInfo] = []
    for label in sorted(by_label, key=lambda lbl: -len(by_label[lbl])):
        indices = by_label[label]
        centroid, cohesion = _centroid_and_cohesion(embeddings[indices])
        clusters.append(ClusterInfo(label=label, member_indices=indices, centroid=centroid, cohesion=cohesion))

    keywords_per_cluster = class_tfidf_top_words(
        [[docs[i].text for i in c.member_indices] for c in clusters], top_n=ctfidf_top_n
    )
    for cluster, keywords in zip(clusters, keywords_per_cluster, strict=True):
        cluster.keywords = keywords

    largest = max((c.size for c in clusters), default=0)
    return StrategyResult(
        method=method,
        clusters=clusters,
        noise_indices=noise,
        duration_ms=duration_ms,
        largest_share=largest / len(docs) if docs else 0.0,
    )
