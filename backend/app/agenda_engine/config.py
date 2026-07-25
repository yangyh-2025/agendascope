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

    # 媒体首发锚点判定（详细设计 4.2 算法 4，T3.6）
    origin_wire_services: list[str] = [      # 通讯社识别名单（大小写不敏感匹配 source.name）
        "Reuters", "AP", "AFP", "Bloomberg", "TASS", "Xinhua",
    ]
    origin_wire_boost_hours: float = 6.0     # 通讯社原文锚点向早于普通媒体最多 N 小时倾斜（估算）
    origin_min_confidence_for_alert: str = "high"  # 低置信首发不自动告警，需人工核实

    # 跟随国序列计算（详细设计 4.2 算法 4，T3.9）
    follower_window_days: int = 14           # 跟随统计窗口（估算）：超过 N 天的"跟随"不计入序列

    # 统计佐证（详细设计 4.2 算法 4 evidence 部分 + 2.129 数据不足 4004，T3.10）
    stats_min_articles: int = 100            # 统计检验最小样本量硬性阈值，<100 拒绝输出误导性结论
    stats_xcorr_max_lag_days: int = 14       # 时滞互相关最大 lag（天，估算）
    stats_granger_max_lag_days: int = 7      # Granger 因果最大 lag（天，估算）
    stats_qap_permutations: int = 1000       # QAP 置换检验次数（估算）
    stats_significance_alpha: float = 0.05   # 显著性水平 α（p < α 判显著）
    stats_window_days: int = 30              # 统计窗默认天数（与 follower_window_days 对齐可按场景调整）

    # 实体库与 NER 提及识别（详细设计 4.2 算法 4，T3.7）
    entity_ambiguity_low_confidence: float = 0.6  # 同名歧义置信度阈值，低于此进人工复核队列（估算）
    entity_blacklist_dampen: float = 0.3          # 黑名单命中降权系数（与 T3.5 黑名单联动，防超级节点虚假关联）
    entity_country_match_boost: float = 1.0       # 上下文 country_code 一致时置信度系数
    entity_country_mismatch_dampen: float = 0.5   # 上下文 country_code 不一致时置信度衰减

    # LLM 首发表述判定器（详细设计 4.2 算法 4 llm_first_utterance，T3.8）
    first_utterance_total_budget: int = 4000     # 总 token 预算（详细设计约束）
    first_utterance_candidate_budget: int = 2000 # 候选全文片段 token 预算（超出截断，不裁剪历史表述）
    first_utterance_history_limit: int = 5       # 实体历史表述摘要取近 N 条（保持时间升序喂入）
    first_utterance_topic_titles_limit: int = 5  # 议题代表标题取 N 条（议题背景）

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
