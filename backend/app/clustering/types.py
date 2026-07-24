"""聚类引擎共享数据结构（策略层与落库层解耦：策略只吃向量/文本，产出簇描述）。"""
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

NOISE_LABEL = -1  # HDBSCAN 噪声点标签约定（入"未归类"池）


@dataclass(frozen=True)
class ClusterDoc:
    """参与聚类的文章快照（PG 读取后的内存表示）。"""

    article_id: UUID
    title: str
    text: str  # c-TF-IDF 分词输入（标题 + 摘要/正文头部）
    language: str
    country_code: str
    published_at: datetime
    embedding: list[float]


@dataclass
class ClusterInfo:
    """单簇产出：成员、质心、凝聚度、c-TF-IDF top 词。"""

    label: int
    member_indices: list[int]
    centroid: list[float]
    cohesion: float  # 成员对质心平均 cosine
    keywords: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.member_indices)


@dataclass
class StrategyResult:
    """单策略一轮产出：簇列表 + 噪声下标 + 评估指标（双策略并行评估口径）。"""

    method: str  # bertopic / agglomerative / keyword_fallback
    clusters: list[ClusterInfo]
    noise_indices: list[int]
    duration_ms: float
    largest_share: float = 0.0  # 最大簇占全部文章比（超大簇黑洞护栏指标）

    @property
    def singleton_count(self) -> int:
        return sum(1 for c in self.clusters if c.size == 1)


class BertopicDegenerateError(RuntimeError):
    """BERTopic 本轮产出不可信（超大簇黑洞 / 拟合失败），调用方回落并行 Agglomerative 结果。"""
