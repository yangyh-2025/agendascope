"""Agglomerative 硬阈值聚类器（T2.7）：cosine 距离阈值 0.25，average linkage。

与 BERTopic 并行评估的对照策略（详细设计 4.1）：
- 硬距离阈值保证簇内平均两两距离受控，不存在 HDBSCAN 式超大簇黑洞
- 不合并进任何簇的点自然成为 size=1 微簇（孤证保留，议题萌芽不丢弃）
- 向量已 L2 归一化，cosine distance = 1 - 点积
"""
import time

import numpy as np

from app.clustering.base import build_result
from app.clustering.config import get_cluster_settings
from app.clustering.types import ClusterDoc, StrategyResult
from app.core.logging import get_logger

logger = get_logger("clustering.agglomerative")


class AgglomerativeClusterer:
    def __init__(self, distance_threshold: float | None = None):
        settings = get_cluster_settings()
        self.settings = settings
        self.distance_threshold = (
            distance_threshold if distance_threshold is not None else settings.agglomerative_distance_threshold
        )

    def cluster(self, docs: list[ClusterDoc]) -> StrategyResult:
        if not docs:
            return StrategyResult(method="agglomerative", clusters=[], noise_indices=[], duration_ms=0.0)
        from sklearn.cluster import AgglomerativeClustering

        embeddings = np.asarray([d.embedding for d in docs], dtype=np.float64)
        t0 = time.perf_counter()
        if len(docs) == 1:
            labels = [0]  # 单样本也保留为孤证微簇，不判噪声
        else:
            model = AgglomerativeClustering(
                n_clusters=None,
                metric="cosine",
                linkage="average",
                distance_threshold=self.distance_threshold,
            )
            labels = [int(lbl) for lbl in model.fit_predict(embeddings)]
        duration_ms = (time.perf_counter() - t0) * 1000
        result = build_result("agglomerative", labels, docs, self.settings.ctfidf_top_n, duration_ms)
        logger.info(
            "agglomerative_cluster_done",
            docs=len(docs), clusters=len(result.clusters),
            singletons=result.singleton_count, largest_share=round(result.largest_share, 3),
            duration_ms=round(duration_ms, 1),
        )
        return result
