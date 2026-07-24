"""BERTopic 主线聚类器（T2.6）：UMAP 降维 + HDBSCAN 密度聚类 + c-TF-IDF 表征。

口径与护栏（详细设计 4.1/4.2、调研教训）：
- 新簇 ≥10 篇（min_cluster_size）且凝聚度达标才保留为候选 Topic，不达标簇解散入噪声
- 噪声点（HDBSCAN label=-1）不建簇，调用方入"未归类"池
- 超大簇黑洞护栏：单簇占比超 max_bertopic_cluster_share 判本轮结果不可信，
  抛 BertopicDegenerateError，由调用方回落并行评估的 Agglomerative 结果
- bertopic/umap/hdbscan 延迟导入：库缺失或拟合异常时降级链可识别"不可用"
"""
import time

import numpy as np

from app.clustering.base import build_result
from app.clustering.config import get_cluster_settings
from app.clustering.tokenize import tokenize
from app.clustering.types import BertopicDegenerateError, ClusterDoc, StrategyResult
from app.core.logging import get_logger

logger = get_logger("clustering.bertopic")


class BertopicClusterer:
    def __init__(
        self,
        min_cluster_size: int | None = None,
        min_cohesion: float | None = None,
        max_cluster_share: float | None = None,
        umap_n_neighbors: int | None = None,
    ):
        settings = get_cluster_settings()
        self.settings = settings
        self.min_cluster_size = min_cluster_size or settings.bertopic_min_cluster_size
        self.min_cohesion = min_cohesion if min_cohesion is not None else settings.bertopic_min_cohesion
        self.max_cluster_share = max_cluster_share or settings.max_bertopic_cluster_share
        self.umap_n_neighbors = umap_n_neighbors or settings.umap_n_neighbors

    def cluster(self, docs: list[ClusterDoc]) -> StrategyResult:
        """对一批文章拟合 BERTopic，返回簇结果；拟合失败/退化抛 BertopicDegenerateError。"""
        if len(docs) < max(self.min_cluster_size, 3):
            raise BertopicDegenerateError(f"样本量 {len(docs)} 不足最小簇规模 {self.min_cluster_size}")
        try:
            from bertopic import BERTopic
            from hdbscan import HDBSCAN
            from sklearn.feature_extraction.text import CountVectorizer
            from umap import UMAP
        except ImportError as exc:
            raise BertopicDegenerateError(f"bertopic 依赖不可用: {exc}") from exc

        embeddings = np.asarray([d.embedding for d in docs], dtype=np.float64)
        n_neighbors = max(2, min(self.umap_n_neighbors, len(docs) - 1))
        model = BERTopic(
            language="multilingual",
            umap_model=UMAP(
                n_neighbors=n_neighbors,
                n_components=self.settings.umap_n_components,
                min_dist=0.0,
                metric="cosine",
                random_state=42,  # 固定种子：同一窗口重算结果可复现（校正前后对比有据）
            ),
            hdbscan_model=HDBSCAN(
                min_cluster_size=self.min_cluster_size,
                metric="euclidean",
                cluster_selection_method="eom",
                prediction_data=True,
            ),
            vectorizer_model=CountVectorizer(tokenizer=tokenize, token_pattern=None, min_df=1),
            calculate_probabilities=False,
            verbose=False,
        )
        t0 = time.perf_counter()
        try:
            labels, _ = model.fit_transform([d.text for d in docs], embeddings=embeddings)
        except Exception as exc:
            raise BertopicDegenerateError(f"bertopic 拟合失败: {exc}") from exc
        duration_ms = (time.perf_counter() - t0) * 1000

        result = build_result("bertopic", [int(lbl) for lbl in labels], docs, self.settings.ctfidf_top_n, duration_ms)
        self._apply_cohesion_gate(result)
        if result.largest_share > self.max_cluster_share:
            raise BertopicDegenerateError(
                f"单簇占比 {result.largest_share:.2%} 超护栏 {self.max_cluster_share:.0%}（疑似超大簇黑洞）"
            )
        logger.info(
            "bertopic_cluster_done",
            docs=len(docs), clusters=len(result.clusters), noise=len(result.noise_indices),
            singletons=result.singleton_count, largest_share=round(result.largest_share, 3),
            duration_ms=round(duration_ms, 1),
        )
        return result

    def _apply_cohesion_gate(self, result: StrategyResult) -> None:
        """凝聚度不达标簇解散：成员并入噪声（宁缺毋滥，避免弱凝聚簇污染议题）。"""
        kept = []
        for cluster in result.clusters:
            if cluster.cohesion >= self.min_cohesion:
                kept.append(cluster)
            else:
                result.noise_indices.extend(cluster.member_indices)
                logger.info(
                    "bertopic_cluster_dissolved",
                    label=cluster.label, size=cluster.size, cohesion=round(cluster.cohesion, 3),
                )
        result.clusters = kept
        largest = max((c.size for c in kept), default=0)
        total = sum(c.size for c in kept) + len(result.noise_indices)
        result.largest_share = largest / total if total else 0.0
