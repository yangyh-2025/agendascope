# 调研报告3：IntelligenceIntegrationSystem (IIS) 借鉴分析
> 来源：https://github.com/SleepySoft/IntelligenceIntegrationSystem (dev 分支, v2 架构)
> Apache-2.0, Python, 380★, 2026-07 仍活跃。OSINT 开源情报整合系统：RSS 优先采集（BBC/半岛/塔斯社/NHK/法广/DW/VOA/新华网等 15+ 多语种媒体）→ LLM 结构化分析 → 聚类/推演。

## 最值得借鉴的设计（按价值排序）
1. **回声消除 + 来源保留 → 首发源识别与跨国跟随链路**：DynamicGraphEngine 把同日相似度≥0.65、3日内≥0.85 的报道折叠为同一节点的 related_docs（跟风报道不进图但实体被吸收）；按 TIME_PUB 排序取最早者为首发源、按国家分组即得跨国传播链路。
2. **主题档案合并（Topic Dossier, topic_id 复用）→ 议题跨天演化、次日自动修正**：新种子先与历史议题向量比对，相似则 merge 到旧议题树（topic_id 复用），否则开新议题；配合在线增量聚类双阈值（T_event=0.85 归簇 / T_dup=0.95 判重）。注意：IIS 该设计为半成品（writeback 是 TODO），合并/分裂回写需自行落地。
3. **LLM 终审 + 人工校正闭环**：图谱生成后 LLM 扮演"高级情报审查官"评逻辑连贯性（1-10 分），<5 分 REJECTED、≥5 COMPLETED；快照状态机 QUEUED/PROCESSING/ANALYZING/COMPLETED/REJECTED/FAILED；人工评分 __MANUAL_RATING__；每条记录全链路时间戳 + prompt 版本 + 模型名，支持换 prompt 后重跑对比。
4. **聚类算法实战教训**：弃用 HDBSCAN（超大簇黑洞、实体引力过载、孤证被当噪声），改用 Agglomerative 硬距离阈值（cosine distance_threshold=0.25, average linkage）；孤证保留为 size=1 微簇（首发单源议题萌芽必须保留）；时间衰减加权池化代替 mean pooling。
5. **动态高频实体黑名单 + 实体日频引擎**：每 24h 统计近 30 天 Top-50 高频实体防超级节点污染关联图；实体日频（LOCATION/GEOGRAPHY/PEOPLE/ORG）作议题萌芽/升温信号。
6. **五要素 + GEOGRAPHY 分离数据模型**：TIME/LOCATION/PEOPLE/ORGANIZATION 结构化抽取 + 国家 ISO 码独立字段。
7. **评分自我校准**：LLM 打分后本地规则引擎二次校准（回顾类压分、高相似低新颖惩罚），专治 AI 评分通胀。
8. **工程解耦**：/collect 端点 + Token 采集处理解耦；爬虫插件热重载；AI 客户端多 key 轮转/余额监控/故障切换。

## 不宜照搬
Flask 服务端渲染单机架构；向量库独立进程 HTTP 调用性能上限；无真正的议题合并/分裂/消亡生命周期管理（只有 merge 设计，无 split 和议题衰减/关闭）——本平台需超越。

## 实时性
RSS 增量抓取（如 BBC 15 分钟一轮）+ 抓取记录防重；聚合分离线（每小时全量聚类 24h 窗）与在线（向量 upsert 事件驱动增量归簇）两层。
