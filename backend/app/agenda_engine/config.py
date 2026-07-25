"""议程引擎配置（pydantic-settings，AGENDA_ 环境变量前缀，与 CLUSTER_/NLP_ 分离）。

阈值口径全部来自详细设计 4.2 算法 1/3/5：回声折叠 0.65/0.85、次日归并 0.85、
消亡 7 天、实体黑名单 Top-50 30 天窗口 48h TTL、修正风暴保护 5 次/24h。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AgendaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENDA_", env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # 回声消除折叠（详细设计 4.2 算法 1，T3.1）：同日 / 3 日内双阈值
    echo_fold_same_day: float = 0.65   # 同日折叠阈值（cosine 相似度）
    echo_fold_3day: float = 0.85       # 3 日内折叠阈值（跨天报道需更高相似度才判跟风）
    echo_lookback_days: int = 7        # 议题内回声消除窗口（对齐议题活跃判定口径，估算）

    # 议题生命周期状态机（详细设计 2.7 / 4.2 算法 3 注释，T3.2）
    lifecycle_archive_days: int = 7    # 连续 N 天无新报道自动归档（估算）
    confirmed_min_size: int = 10       # ≥ N 篇进 confirmed（与 clustering 口径一致）

    # 次日自动归并（详细设计 4.2 算法 3，T3.3）
    merge_sim: float = 0.85            # 跨语言向量比对归并阈值（估算）
    merge_candidate_k: int = 5         # HNSW 近邻候选数
    merge_active_days: int = 30        # 参与归并比对的历史活跃议题窗口（估算）
    merge_batch_size: int = 200        # 单轮归并处理的候选议题上限（防失控）
    merge_interval_minutes: int = 60   # 次日归并触发周期（与重聚类校正同节拍可配）

    # 修正风暴保护（详细设计 4.2 算法 4 注释，T3.14 预留）
    revision_storm_threshold: int = 5  # 单议题 24h 修正 > N 次冻结自动修正转人工（估算）

    # 消亡扫描周期（T3.2）
    sweep_interval_minutes: int = 60   # 消亡扫描触发周期

    # 动态高频实体黑名单（详细设计 4.2 算法 5，T3.5）
    entity_blacklist_top_k: int = 50         # 高频实体 Top-K（估算 50）
    entity_blacklist_window_days: int = 30   # 统计窗口（近 N 天 articles 实体日频）
    entity_blacklist_ttl_hours: int = 48     # Redis Set TTL（刷新失败保旧值不丢）
    entity_blacklist_refresh_hours: int = 24  # 刷新周期（每日）

    # 议程引擎 worker（app.worker.agenda_worker，M3-1 收尾接入）
    worker_poll_seconds: float = 60.0  # 主循环节拍（归并/消亡/黑名单按各自周期到期触发）

    @property
    def entity_blacklist_key(self) -> str:
        return "entity:blacklist"  # 黑名单实体集合（Set 结构，SISMEMBER O(1) 查）

    @property
    def entity_blacklist_updated_at_key(self) -> str:
        return "entity:blacklist:updated_at"  # 最近刷新 ISO 时间戳（运维观测用）


@lru_cache
def get_agenda_settings() -> AgendaSettings:
    return AgendaSettings()
