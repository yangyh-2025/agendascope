"""聚类引擎配置（pydantic-settings，CLUSTER_ 环境变量前缀，与 NLP_ 分离独立调参）。

阈值口径全部来自详细设计：T_event=0.85 / T_dup=0.95（算法 2）、
Agglomerative cosine 0.25 average linkage、新簇 ≥10 篇、"未归类"池 48h 粗分。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ClusterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLUSTER_", env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # 在线增量归簇（算法 2：双阈值事件驱动）
    t_event: float = 0.85   # 归簇阈值（与活跃议题质心 cosine）
    t_dup: float = 0.95     # 判重阈值（与近期文章 cosine，命中标记转载不重复建簇）
    active_topic_days: int = 30  # 参与在线归簇比对的活跃议题窗口（对齐次日归并档案口径）

    # 质心时间衰减加权池化（IIS 教训：非 mean pooling），半衰期越短新证据权重越高
    centroid_half_life_hours: float = 24.0

    # BERTopic 主线（T2.6）：新簇 ≥10 篇且凝聚度达标才建 Topic；噪声入"未归类"池
    bertopic_min_cluster_size: int = 10
    bertopic_min_cohesion: float = 0.5  # 簇成员对质心平均 cosine 下限，不达标簇解散入噪声
    umap_n_neighbors: int = 15
    umap_n_components: int = 5
    max_bertopic_cluster_share: float = 0.8  # 单簇占比护栏：超限判 HDBSCAN 超大簇黑洞，弃用本轮结果
    bertopic_timeout_seconds: int = 600  # BERTopic 拟合超时护栏（低内存服务器 UMAP/HDBSCAN 慢但能完成，实测 ~200s fit+初始化；600s 只防真卡死，不误伤正常完成）
    bertopic_enabled: bool = True  # 主策略开关：低内存 2G 服务器 BERTopic 峰值 ~500MB + 落库超 640m 会 cgroup OOM 杀容器（实测），关闭后直接走 Agglomerative（精度略降但内存安全）

    # Agglomerative 硬阈值并行策略（T2.7）：cosine 距离阈值 0.25，average linkage；孤证保留 size=1
    agglomerative_distance_threshold: float = 0.25

    # c-TF-IDF top 词（topics.keywords ≤20，详细设计 2.7）
    ctfidf_top_n: int = 20

    # 生命周期初版（T2.10）：size=1 nascent / <confirmed_min_size forming / ≥ confirmed
    confirmed_min_size: int = 10
    unclassified_ttl_hours: int = 48  # "未归类"池超时不成就按关键词粗分

    # 每小时全局重聚类校正（T2.9）
    recluster_interval_minutes: int = 60
    recluster_window_hours: int = 24   # 近 24h 窗重算（详细设计 4.2 算法 2 注释）
    recluster_min_docs: int = 6        # 窗内文章数低于此值不值得重聚类（UMAP/HDBSCAN 最小样本约束）
    snapshot_ttl_seconds: int = 86400  # 快照保留时长（覆盖至下次校正发布）

    # 聚类质量监控（T2.9 增强）：簇内凝聚度/簇间分离度跌破阈值写 P1 漂移告警（防抖）
    cohesion_alert_threshold: float = 0.4    # 簇内凝聚度均值下限（低于说明簇内异质）
    separation_alert_threshold: float = 0.3  # 簇间分离度（最近邻簇质心 cosine）下限（低于说明簇间粘连）
    quality_alert_debounce_seconds: int = 21600  # 质量告警防抖 6h

    # 关键词降级链（T2.11）：历史议题 keywords 重叠 ≥ 此值归入该议题
    keyword_min_overlap: int = 2
    alert_debounce_seconds: int = 3600  # P1 告警防抖（对齐源失败率告警 1h 口径）

    # 代表标题条数（供 LLM 命名器取簇内代表报道，T2.13 输入 5–10 条）
    representative_titles_n: int = 10

    # cluster worker 消费参数（与 NLP worker 同款语义）
    worker_group: str = "cluster"
    worker_batch_size: int = 32
    worker_block_ms: int = 5000
    worker_reclaim_idle_ms: int = 60000
    worker_max_attempts: int = 8

    @property
    def snapshot_key(self) -> str:
        return "cluster:snapshot:latest"  # 详细设计缓存层：重聚类校正快照 L2

    @property
    def snapshot_status_key(self) -> str:
        return "cluster:snapshot:status"  # ready / correcting（读侧据此标注"校正中"）

    @property
    def degraded_flag_key(self) -> str:
        return "cluster:degraded:keyword_fallback"  # 降级起始时间戳（恢复后回填依据）


@lru_cache
def get_cluster_settings() -> ClusterSettings:
    return ClusterSettings()
