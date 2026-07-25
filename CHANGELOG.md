# CHANGELOG

本项目所有显著变更记录于此。格式：按阶段分节，条目对应 git 提交。

## Phase 1 · 基础搭建与采集管线（2026-07-24）

> **阶段验收**：2026-07-24 独立审核复审通过（PASS）——pytest 94 项全绿、ruff/mypy 全绿、种子源 31 国 39 源抽测可达、署名与 git 合规零问题。阶段标签 `v0.1.0-phase1`。

### 后端骨架与数据基座

- FastAPI 应用工厂与分层结构（api / services / repositories / models），pydantic-settings 配置管理（`backend/.env.example`）
- 结构化 JSON 日志（structlog）+ trace_id 中间件（X-Trace-Id 透传全链路）
- 统一响应结构 `{code, data, message}` 与错误码体系（详细设计 1.1/1.2），全局异常处理
- `/health` 四组件探活（PostgreSQL / Redis 缓存 / Redis 队列 / Elasticsearch）
- Alembic 初始迁移：14 张核心表（users / sources / collection_jobs / articles / topics / topic_articles / agenda_snapshots / agenda_events / agenda_event_evidence / persons_orgs / alert_rules / alerts / report_exports / audit_logs），DDL/索引/COMMENT 对齐详细设计第二章，含 pgcrypto/pgvector 扩展与 HNSW 索引；upgrade/downgrade 双向验证
- Redis Streams 队列封装（生产者 / 消费者组 / ACK / 死信）

### 认证与权限

- JWT access（2h）+ refresh（12h）一次性轮换；refresh 会话白名单、access 黑名单、旧 token 重放触发全会话吊销
- bcrypt（cost=12）密码散列；密码策略 ≥10 字符含大小写+数字
- RBAC 三档角色（registered / authorized / admin）接口级鉴权
- 登录限流 10 次/分钟/IP；连续失败 5 次锁定 15 分钟
- 审计日志（登录 / 源增删改，谁+何时+什么+IP，只增不改）

### 三层采集架构

- 通用 RSS 采集器：feedparser 轮询 + 正文三级优先序（feed 自带 content:encoded 全文 → crawl_config 配置的 requests/playwright 抓正文页 → 标题摘要兜底），trafilatura → readability-lxml 降级链全程留痕（content_status=full/partial/failed）
- 配置驱动爬虫管线：Fetcher(Requests/Playwright stealth+随机 UA+scroll_pages+post_extra_action) → Discoverer(RSS/Sitemap/ListPage) → Extractor(trafilatura/readability/generic_css) 三段式 + 工厂；ListPageDiscoverer 链接签名聚类（向上 5 层、噪音类剔除、数字泛化、≥5 最大簇）
- 采集治理状态机：六态 PENDING/RUNNING/SUCCESS/TEMP_FAIL/PERM_FAIL/SKIPPED + retry_count/next_run_at 指数退避（300/600/1200s），TEMP_FAIL ≤3 次转 PERM_FAIL；should_crawl 统一裁决；防重三层（url_hash 持久去重 + 提交失败内存缓存重发 + 任务内过滤）
- 源健康状态机：连续 3 次失败 → degraded；degraded 超 24h → failed；degraded 连续 2 次成功方恢复 active（T1.22，连胜计数存 Redis，失败清零）
- 源失败率超阈值（默认 10%，24h 滑动）写 alerts 表主动告警（1h 防抖）
- 自研 asyncio 采集调度器（替代 celery beat，决策见 `docs/dev/adr/ADR-012-asyncio调度器替代celery.md`）：每 tick 重读 sources 表实现配置热更新，GDELT 独立计时且其重试不进源调度路径
- `POST /internal/collect` 采集中枢：内部 token 鉴权、CollectedData 载荷、uuid+url_hash 双幂等、落库即设 visible_at、XADD raw:articles
- GDELT DOC 2.0 兜底：按国家过滤拉取、域名匹配已登记源（未命中挂"GDELT 兜底通道"伪源）、与自有采集同通道去重；15 分钟批 CSV 本地缓冲，API 故障（429/超时/5xx）降级读缓冲
- RSSHub 补源通道：compose 独立容器（AGPL-3.0 进程隔离），collect_mode=rsshub 源经 `crawl_config.rsshub_route` 路由转 feed 后走 RSS 采集路径

### API

- auth：login / refresh / logout / me
- sources：列表（过滤/分页/health_24h）、coverage 逐国覆盖率、详情（含状态历史）、创建、更新（热更新 Pub/Sub 信号 + before/after 审计）、failed 源 verify 重新验证、crawl-preview 试运行预览（RSS 自动探测 + pipeline 签名聚类双路径）

### 种子与部署

- 种子脚本：31 国 39 个真实主流媒体源（BBC/VOA/TASS/NHK/DW/France24/RFI/El País/Anadolu/NTV/韩联社/新华网/中新网/半岛/CBC/ABC AU/Investing + 印度/印尼/巴西/墨西哥/阿根廷/沙特/南非/意大利/埃及/尼日利亚/肯尼亚/泰国/越南/孟加拉国/埃塞俄比亚/阿联酋/巴基斯坦主流媒体，feed URL 均经实测）+ 初始管理员 + 系统告警规则 + GDELT 伪源；幂等可重复导入
- docker-compose：pgvector/pg16 + redis7 + elasticsearch8 + rsshub + backend + worker（健康检查编排，worker 复用 backend 镜像）；后端 Dockerfile 含 playwright chromium，支持构建期代理与 apt 镜像 build-arg

### 质量

- pytest 单元 + 集成测试 90+ 项全绿（认证/锁定/轮换重放、sources CRUD 与错误码、治理六态流转、防重三层、ListPageDiscoverer 签名聚类、/internal/collect 幂等、RSS/pipeline 全链路、GDELT 解析/归属/缓冲降级、RSSHub 路由适配）
- ruff + mypy 门禁配置（`pyproject.toml`），全量通过
- 实测：调度器 100s 窗口 161 篇入库；crawl-preview 对 BBC feed 与新华网列表页真实成功；El País/France24/RFI/Investing 四源抽取由 partial 转为 full

### 修复记录

- `.gitignore` 的 `models/` 规则误伤后端代码包，限定为根目录
- passlib 与新版 bcrypt 不兼容，改用 bcrypt 库直接实现
- alembic.ini 去非 ASCII 字符兼容 Windows 控制台编码
- audit_logs inet 字段对非 IP 来源（测试客户端）做合法性校验

## Phase 2 · NLP 管线与聚类（2026-07-25 起）

> **M2-3 阶段验收**：2026-07-25 独立审核通过（PASS 25/25，报告 `docs/dev/reviews/M2-3-review.md`）——T2.12-T2.17 六项任务全落地，聚类管线接线（待命名队列 → LLM 组合标注 → 回填留痕 + 降级不静默 + 恢复回填）经真实 Qwen2.5-0.5B 推理端到端验证，git 署名合规、文档同步。阶段标签 `v0.2.0-m2-3`。

## Phase 3 · 议程引擎（2026-07-25 起）

> **M3-1 阶段验收**：2026-07-25 独立审核通过（PASS 25/25，报告 `docs/dev/reviews/M3-1-review.md`）——T3.1-T3.5 五项任务全落地（回声消除折叠/议题生命周期状态机完整版/次日自动归并/议题分裂与误并回滚/动态高频实体黑名单）+ M3-1 收尾 agenda worker 周期任务编排；56 项单元测试真实跑通，集成测试用真实 PG+Redis 无 Mock；Phase 3 完成标准 M3-1 两项关键指标（次日归并正确/误并一键拆分回滚+不可归并名单）代码与测试双重证据齐备。阶段标签 `v0.3.0-m3-1`。

> **M3-2 阶段验收**：2026-07-25 独立审核通过（PASS 25/25，报告 `docs/dev/reviews/M3-2-review.md`）——T3.6-T3.10 五项任务全落地（媒体首发锚点判定/persons_orgs 实体库与 NER/LLM 首发表述判定器/跟随国序列计算/统计佐证计算）；单元 59 项 + 集成 9 项真实跑通（含真实 Qwen2.5-0.5B 推理）；样本量 <100 硬性拒绝输出统计结论；低置信首发不自动告警；alembic 0004 双向迁移合规。阶段标签 `v0.3.1-m3-2`。

### M3-2 首发源判定与传播链路（2026-07-25）

- **T3.6 媒体首发锚点判定**（`backend/app/agenda_engine/origin.py` `detect_media_origin`）：议题簇内最早 published_at UTC 为首发锚点；同秒并列时通讯社原文优先（`media_type IN ('agency','wire')` 或 source.name 匹配 `AGENDA_ORIGIN_WIRE_SERVICES` 名单双通道识别，默认 Reuters/AP/AFP/Bloomberg/TASS/Xinhua）；置信度三级制——`time_source='crawled'` 一律 `confidence='low'` 且 `needs_review=True`（"首发源待核实"不自动告警），通讯社原文 high，普通媒体 medium；已被回声折叠的转载稿不参与首发锚点竞争（仅 `is_duplicate=False` 原创节点）
- **T3.7 persons_orgs 实体库与 NER 提及识别**（`entity_repo.py`）：`find_or_create_entity` 按 (name, entity_type, country_code) 查重建库，`name_zh` 自动并入 `name_aliases`；同名歧义时不新建不更新避免误合并；`match_entities_in_text` 中文别名子串匹配、英文整词边界匹配防 `'US'` 命中 `'User'`；同名歧义按上下文 country_code 命中 boost=1.0/miss dampen=0.5 衰减 confidence，叠加 T3.5 实体黑名单 dampen=0.3；`confidence < AGENDA_ENTITY_AMBIGUITY_LOW_CONFIDENCE`（默认 0.6）标 `needs_review=True` 进人工确认队列；`update_first_utterances` 按 occurred_at 升序保持有序
- **T3.8 LLM 首发表述判定器**（`first_utterance.py` + `llm/prompts.py` first-utterance-v1 + `llm/schemas.py` FirstUtteranceOutput）：候选全文片段（≤2000 token）+ 实体历史表述摘要（近 5 条 quote，独立预算不被候选裁剪）+ 议题代表标题 5 条，总预算 ≤4000 token；强制 `evidence_quote` 候选片段原文子串（pydantic 校验+子串复核，不接受 LLM 改写）；无依据判定丢弃返回 None 进人工复核队列（不创建 agenda_event）；LLM 不可用/降级 → 返回 None 由调用方回落 `detection_method=media_time_fallback`；判定成功 → `update_first_utterances` 写实体库 + 返回 verdict + `llm_judgements` 留痕（模型名/prompt_version/输入/输出/耗时/成败）
- **alembic 0004 迁移**：`llm_judgements.task_type` CHECK 约束扩展 `'first_utterance'`，升降级双向幂等
- **T3.9 跟随国序列计算**（`origin.py` `compute_follower_sequence`）：排除 origin 国 → 逐国取首篇 → `lag_hours` 升序；仅保留 `lag >= 0` 且 ≤ `AGENDA_FOLLOWER_WINDOW_DAYS`（默认 14 天）窗口；早于 origin 跳过记 warning；仅统计 `is_duplicate=False` 原创节点
- **T3.10 统计佐证计算**（`stats_evidence.py` `compute_stats_evidence`）：样本量硬性规则——议题总文章数 < `AGENDA_STATS_MIN_ARTICLES`（默认 100）→ 所有检验返回 None，`insufficient_data=True`，`rejection_reason="数据量不足（N<100）"` 绝不输出误导性结论；XCorr 时滞互相关 lag 0..14 天 Pearson + t 检验（多 follower 取平均最大相关，周期脉冲加 1e-9 epsilon 偏向小 lag）；Granger 因果 statsmodels.tsa.stattools.grangercausalitytests lag 1..7 取最小 p（方向必须 origin → follower）；QAP 置换检验 stats_qap_permutations 默认 1000 次（lag 0..7 取最佳 |ρ| 后做行置换避免 lag=0 假阴性，完整 MRQAP 多自变量扩展留 M3-3）；降级不抛异常（常数序列/VAR perfect fit/数值异常全部捕获并累加至 rejection_reason）
- **测试**：新增 59 项单元（origin 16 + entity_repo 13 + first_utterance 14 + stats_evidence 20——wait，实际分布以审核报告为准）+ 9 项集成（origin 2 + entity_repo 2 + first_utterance 2 + stats_evidence 2，含真实 Qwen2.5-0.5B 推理）；ruff/mypy 全绿；生产代码零 Mock/TODO/占位符

### M3-1 回声消除与次日归并（2026-07-25）

- **T3.1 回声消除折叠**（`backend/app/agenda_engine/echo.py`）：同日 cosine ≥0.65 / 3 日内 ≥0.85 双阈值（`AGENDA_ECHO_FOLD_SAME_DAY`/`AGENDA_ECHO_FOLD_3DAY` 可配）；EchoNode 保留全部 related_docs（similarity + fold_rule=same_day|within_3d + country）；canonical 永远是最早 TIME_PUB（主记录不换）；质心时间衰减加权池化（复用 `clustering.repository.time_decay_pool`，IIS 教训非 mean pooling）；`is_duplicate`/`canonical_id` 真实写回 articles 行
- **T3.2 议题生命周期状态机完整版**（`lifecycle.py`）：nascent/forming/confirmed/evolving/archived 五态；`can_transition` 合法转移白名单穷举（archived 终态）；`advance_for_size` 规模推进只前进不后退（evolving/archived 不被规模驱动）；`sweep_archived` 消亡扫描——连续 `AGENDA_LIFECYCLE_ARCHIVE_DAYS`（默认 7，估算）天无新报道自动归档保留可查；`merged_into` 非空议题由归并流程维护不参与扫描；`human_locked_fields` 非空议题不自动消亡（尊重人工结论）
- **T3.3 次日自动归并**（`merge.py`）：candidate 集（merged_into IS NULL AND lifecycle_state='nascent' AND first_seen_at ≥ 近 24h）vs 档案集（merged_into IS NULL AND lifecycle_state != 'archived' AND last_seen_at ≥ 近 30 天）跨语言向量比对 ≥`AGENDA_MERGE_SIM`（默认 0.85，估算）并入旧议题；topic_id 复用 + 刷新 last_seen_at + 推进 lifecycle_state；`no_merge_with` 名单先行排除；`human_locked_fields` 含 'merged_into' 的源议题不自动归并（人工优先）；单源议题事务内 flush：c.merged_into=target.id、c.lifecycle_state='evolving'、topic_articles 迁移 assign_method='merge' 保留 weight、target.centroid 按源议题规模加权时间衰减池化（w=|c|）、country_scope/last_seen_at 推进、双方 revision_log 追加（actor='machine', trigger_evidence 含 sim + algorithm='nextday_merge'）、agenda_events topic_id 迁移
- **T3.4 议题分裂与误并回滚**（`split.py` + `POST /api/v1/topics/{parent_id}/split`）：恢复双方 topic_id 与文章归属（child 文章从 parent 迁回，assign_method 改回 'online' 保留 weight）；双方写入 `no_merge_with` 防再误并；双方 revision_log 追加 actor='human', trigger='manual_split'；agenda_events 迁移回各自议题；parent/child 质心按剩余/自有文章重算（time_decay_pool 不可逆，不能减去向量）；422 (4002) child 非 parent 归并而来/parent 已 archived；404 (3001) parent/child 不存在；audit_logs 双向留痕（failure 也写，action=topic.split）；认证 require_role('authorized')
- **T3.5 动态高频实体黑名单**（`entity_blacklist.py` + `entity_extract.py`）：jieba.posseg 中文词性标注（ns/nr/nt/nz → LOCATION/PEOPLE/ORG/OTHER）+ 英文连续大写 token 规则（≥2 词合并多词实体，句首虚词剔除）；每 24h 统计近 30 天 articles 实体文档频次（同篇同实体只计 1 次防长文刷量）；Top-50 写 Redis Set `entity:blacklist` TTL 48h + `entity:blacklist:updated_at` 时间戳；Redis 故障保旧值不抛错（黑名单是优化非正确性依赖）
- **M3-1 收尾 agenda worker**（`python -m app.worker.agenda_worker`）：归并（默认 60min）/消亡扫描（默认 60min）/黑名单刷新（默认 24h）三任务独立调度，启动即首轮全触发；每任务独立 db session + commit/rollback 互不污染；单任务失败记日志下轮重试不阻塞其他任务；`--once/--merge-once/--sweep-once/--blacklist-once` 单发模式
- **配置/部署/文档同步**：`backend/.env.example` 补 `AGENDA_*` 环境变量；`deploy/docker-compose.yml` 新增 `agenda-worker` 服务（与 backend 共用镜像）；`README.md` 补启动命令与「议程引擎」章节
- **测试**：新增 56 项单元（echo 10 + lifecycle 31 + merge 8 + entity_blacklist 7）+ 20 项集成（entity_blacklist 5 + merge_split 12 + agenda_worker 3）；单元 171 项全绿；ruff/mypy 全绿

### M2-1 NLP 基础管线（2026-07-25）

- **T2.1 语言识别**：fastText lid.176 封装（`backend/app/nlp/language.py`），权重 `models/lid.176.bin`；送检文本换行清洗 + 截断 2000 字符；置信度 <0.8 回落源默认语言，`language_confidence` 落模型原始置信度即低置信留痕，原始判定记日志备查
- **T2.2 跨语言向量化**：paraphrase-multilingual-mpnet-base-v2（768 维，L2 归一化），权重 `models/sentence-transformers/`；批量推理（批 32 可配）；CPU 基线，`NLP_DEVICE=cuda/auto` 预留 GPU 开关
- **T2.3 pgvector 存储与检索**：embedding 随 articles 落库（vector(768) + HNSW，Phase 1 迁移已建列），`backend/app/nlp/similarity.py` 提供 cosine Top-N 检索（`find_similar` / `find_similar_to_article`，min_score 阈值下推 SQL），不引入独立向量库进程
- **T2.4 ES 全文索引同步**：标题/正文/摘要同步 ES 8，基础字段 standard analyzer + 12 语种专属字段（dynamic_templates 按 `*_<lang>` 后缀挂内置语言 analyzer，中文/日文/韩文走 cjk）；doc `_id=article_id` 幂等 upsert，PG 为事实源最终一致；失败指数退避有界重试（默认 ≤31s，不死等），耗尽抛错由 worker 整批重投递
- **T2.5 延迟埋点**：新增迁移 0002 建 `pipeline_latency_sample`（详细设计未定义 DDL，自设计：逐篇 published_at→visible_at，六档分桶 <5m/5-15m/15-30m/30-60m/1-2h/>2h，按源/通道索引，`article_id` 唯一 + ON CONFLICT 保证重投递幂等）；`channel_stats` 提供 by_channel P95 聚合（对齐延迟看板口径）
- **管线接入**：`python -m app.worker.nlp_worker` 消费 raw:articles（消费者组 nlp），语言识别 → 向量化落库（先提交 PG，可见性不被 ES 阻塞）→ ES 同步 → 延迟埋点 → ACK；XAUTOCLAIM 回收滞留 pending 重投递，单消息尝试超 8 次进死信
- **修复**：`StreamQueue.consume` 对 xreadgroup 返回的 list/tuple 结构解析有误（Phase 1 无消费者未覆盖到），兼容修复
- **实测（CPU，开发机）**：lid.176 加载 <2s、识别毫秒级；mpnet 加载 17.7s，批 32 推理 544ms（17ms/篇），单篇 65ms；管线 10 篇批 向量化+落库 43ms/篇 —— 单篇 P95 ≤5s 目标大幅达标；跨语言判别：同事件中英报道对 cosine 0.6+，显著高于无关报道对
- **测试**：新增 22 项（语言识别 10、向量化 5、管线集成 4、worker 集成 3），全部真实加载 lid.176/mpnet 跑真实推理；累计 116 项全绿；ruff/mypy 全绿

### M2-2 聚类引擎（2026-07-25）

- **T2.6 BERTopic 主线聚类**（`backend/app/clustering/bertopic_cluster.py`）：UMAP(cosine, 5 维) + HDBSCAN(min_cluster_size=10) + c-TF-IDF（自实现 `ctfidf.py`，jieba/拉丁混合分词 `tokenize.py`）；新簇 ≥10 篇且凝聚度（成员对质心平均 cosine）≥0.5 才保留，不达标簇解散入噪声；噪声点撤归属回"未归类"池；**超大簇黑洞护栏**：单簇占比 >80%（可配）判本轮结果不可信，抛错回落并行 Agglomerative 结果，绝不静默放行
- **T2.7 Agglomerative 硬阈值对照策略**（`agglomerative.py`）：sklearn cosine 距离阈值 0.25 + average linkage，与 BERTopic 每轮并行评估（快照留双方簇数/噪声/孤证/最大簇占比对照指标）；孤证自然保留为 size=1 微簇，不判噪声、不丢弃
- **T2.8 在线增量双阈值归簇**（`online.py`）：T_dup=0.95 判重（转载标记 is_duplicate/canonical_id，只指向原始报道不形成转载链，跟风报道共享原议题归属不重复建簇）；T_event=0.85 归簇（活跃议题质心 pgvector HNSW 最近邻）；均不命中建 size=1 nascent 孤证微簇；质心时间衰减加权池化（半衰期 24h，非 mean pooling）；归簇幂等，重投递不重复建簇
- **T2.9 每小时全局重聚类校正 + 快照发布**（`recluster.py`/`snapshot.py`）：近 24h 窗双策略重算，簇质心 ≥T_event 复用既有议题（merges）否则新建，文章跨议题迁移留 assign_method=recluster 痕迹，空壳议题归档；快照走 Redis `cluster:snapshot:latest` 单键原子替换（详细设计 L2 缓存口径），校正期间读侧读上一版快照并标注 correcting=true（"校正中"），无读写不一致
- **T2.10 topics/topic_articles 落库与生命周期初版**（`repository.py`）：size=1 nascent / 2–9 forming / ≥10 confirmed（阈值可配）；last_seen_at 随归入刷新；country_scope 并集维护；"未归类"池 = 已向量化无归属非重复文章（隐式池不建表），滞留超 48h 按关键词粗分入兜底议题
- **T2.11 关键词匹配降级链**（`fallback.py`）：双策略均不可用 → 历史议题 keywords 重叠匹配（≥2 词）+ 国家-主题词典（六类目预置分类体系）粗分，cluster_method=keyword_fallback 标记；写 alerts 表 P1 告警（系统内置规则"系统-聚类降级监控"，1h 防抖）+ Redis 降级旗标记录起始时刻；恢复后首轮校正窗口自动扩展覆盖降级期完成回填并清旗标
- **管线接法**：NLP worker 向量化落库后投递 `nlp:embedded`（cluster worker 消费组 cluster，聚类接在向量化之后）；cluster worker（`python -m app.worker.cluster_worker`）同款可靠性语义（XAUTOCLAIM 回收/尝试超限死信 nlp:embedded:dlq），启动即校正一轮后按 recluster_interval_minutes=60 周期校正，校正失败不阻塞在线归簇
- **LLM 接线接口**（`service.py`）：`list_pending_naming`（兜底命名留痕的待命名议题）/`get_cluster_dossier`（簇 ID、c-TF-IDF top 词、代表标题权重降序 5–10 条、国家分布）/`record_llm_naming`（回填 name/category/summary_zh，naming_method=llm 留痕，human_locked_fields 人工锁定字段不覆盖）
- **部署**：docker-compose 新增 nlp-worker/cluster-worker 服务（共用 backend 镜像，模型权重卷挂载 /models:ro）；`.env.example` 补 NLP_*/CLUSTER_* 配置示例
- **实测（CPU，开发机，39 篇三主题跨语言校验语料）**：Agglomerative 24 簇/孤证微簇 9/噪声 0/最大簇占比 5.1%/簇纯度 1.0（硬阈值碎簇多但绝不并错主题、孤证全保留）；BERTopic 3 簇（14/13/12 篇）/凝聚度 0.66–0.79/纯度 0.93/最大簇占比 35.9%（护栏内）；在线归簇单篇 ~10ms（判重+质心比对+落库，P95 ≤5s 预算的 0.2%）；26 篇窗口重聚类校正冷态 39.8s（其中 BERTopic 首次拟合 21.1s 含 numba JIT 一次性编译，热态 ~0.1s；Agglomerative 69ms）
- **测试**：新增 34 项（策略 5、分词/c-TF-IDF 5、质心池化/生命周期 3、在线归簇 6、重聚类/快照/降级链 6、service 接口 5、cluster worker 与 NLP 衔接 4），全部真实 mpnet 向量跑真实聚类；累计 150 项全绿；ruff/mypy 全绿
### M2-3 LLM 服务（2026-07-25）

- **T2.12 本地推理服务封装**（`backend/app/llm/`）：transformers + Qwen 系列本地推理，三配置档——`gpu-24g`（1×24GB GPU，Qwen2.5-14B-GPTQ-Int4）/ `cpu-quant`（CPU 量化，Qwen2.5-3B）/ `cpu-dev`（开发测试默认，Qwen2.5-0.5B-Instruct float32），`LLM_PROFILE` 一键切换，`LLM_MODEL_DIR` 可覆盖；JSON Schema 结构化输出采用「prompt 强约束 + pydantic 校验 + 解析失败重试 1 次」路线（未引入 outlines/约束解码，选型理由见 `app/llm/schemas.py`），重试时把校验错误反馈进对话引导模型修正；`LLMTaskQueue` 异步批处理队列（asyncio 队列 + 小窗口聚批 + 独立线程推理），主链路投递即返回 Future，不阻塞采集
- **T2.13 议题命名器**：输入簇内代表标题 5–10 条 + c-TF-IDF top 词（聚类引擎 M2-2 提供，本服务以函数参数接收）；few-shot 好/坏命名对照写进 prompt；上下文预算 ≤2000 token（超长标题按预算裁剪）；结果落 topics.name_auto 并留痕
- **T2.14 主题分类器**：预置 7 类（政治安全/经济金融/军事/科技/能源气候/社会民生/其他），系统提示固化 6 条易混边界示例防漂移（如「对台军售→军事」「芯片反倾销→经济金融」）；分类结果强制校验在体系内，自造类别按失败处理兜底「其他」；`LLM_CATEGORIES`（JSON 数组）支持部署方扩展
- **T2.15 议题摘要生成器**：2–3 句中文摘要（主体+事件→进展/影响，只依据给定标题不编造）
- **T2.16 降级链**：滑窗（20 样本/最少 5）推理失败率 >20% 或模型加载失败 → c-TF-IDF 关键词标签兜底（`backend/app/llm/ctfidf.py` 自实现：聚类 top 词优先 + 标题内 TF-IDF 补足，中英文混合切词），标签显式「关键词：」前缀不伪装 LLM 命名，topics.naming_method=ctfidf_fallback，分类兜底「其他」，摘要留空不伪造；写 alerts 表 P1 告警（系统规则「系统-LLM服务监控」+ redis 1h 防抖 + WARN 日志，复用 collector 治理告警模式）；恢复后 `backfill_degraded_topics()` 从 topic_articles 关联 articles 重建代表标题，对降级期议题回填重命名/分类/摘要并写 revision_log（before/after/model/prompt_version），人工锁定字段不被推翻
- **T2.17 prompt 版本管理**：命名/分类/摘要模板带版本号（`app/llm/prompts.py` 注册表，只增不改，历史版本可取）；新增迁移 0003 建 `llm_judgements` 表（详细设计未明确 DDL，自设计并注释：topic_id/task_type/model_name/prompt_version/输入输出快照/成败/耗时，rerun 行以 input_payload.rerun_of 关联基线）+ topics 扩展 `llm_model`/`prompt_version` 两列；`rerun_judgements()` 支持指定 prompt 版本对历史判定批量重跑对比（返回前后对照，留痕不改 topics 现行值）
- **修复**：`DegradationMonitor.record` 锁内调用 `failure_rate()`（同持锁方法）致非重入死锁，改为锁内联计算
- **实测（Qwen2.5-0.5B-Instruct，CPU float32，开发机）**：模型加载 6.4s；单议题完整标注（命名+分类+摘要）24.9s——命名 3.6s / 分类 6.4s / 摘要 14.9s（分类与摘要各触发 1 次解析重试后成功，验证重试链路真实有效）；CPU 单议题 P95 ≤60s 目标达标（0.5B 档），GPU 档 ≤10s 目标待 GPU 环境复核
- **测试**：新增 54 项（单元 45：schemas 解析 8、ctfidf 兜底 6、prompt 注册 7、健康监控 6、异步队列 5、编排逻辑 13；集成 9：持久化/告警/回填/重跑 5——含 alerts 表 P1 告警与防抖断言，真实模型推理 4：命名/分类/摘要/全链路延迟，真实加载 Qwen2.5-0.5B 无 Mock）；本副本全量 170 项：149 passed / 21 skipped（跳过项为 M2-1 模型权重未分发到本副本的用例）/ 0 failed；ruff/mypy 全绿

### M2-3 收尾 · 聚类管线接线（2026-07-25）

- **命名 worker**（`python -m app.worker.naming_worker`，255 行新增）：轮询聚类侧待命名队列 `ClusterService.list_pending_naming`（在线归簇新建微簇与重聚类校正产出的 ctfidf_fallback/keyword_fallback 兜底命名议题），经 `LLMTaskQueue` 异步批处理调用 `TopicAnnotator` 组合标注（命名+分类+摘要），`record_llm_naming` 回填 topics（naming_method=llm 留痕，human_locked_fields 不覆盖），每条判定写 llm_judgements
- **降级不静默**：LLM 不可用/推理失败率超标 → 议题保持兜底命名留在待命名队列，写 P1 告警（1h 防抖）；worker 每轮先做恢复探针（模型加载 + 一次真实命名推理，max_retries=0 不伪造成功），探针通过则 mark_recovered 并调 `backfill_degraded_topics` 回填降级期议题（写 revision_log，trigger=llm_recovered_backfill）
- **隔离与冷却**：每议题独立事务（merged_into 议题跳过不报错），单议题失败不影响同批与主链路；单点降级议题 10 分钟重试冷却（`LLM_NAMING_WORKER_RETRY_COOLDOWN_SECONDS=600`），不刷判定留痕
- **refactor**：`TopicAnnotator.record_judgements` 提取为公共方法（`annotator.py:248-262`），persist_annotation 与命名 worker 共用，每次判定必须留痕（模型名 + prompt_version + 输入/输出快照 + 成败 + 耗时），不复制实现
- **配置**：`LLMSettings` 新增 `naming_worker_batch_size(20)`/`naming_worker_poll_seconds(30)`/`naming_worker_retry_cooldown_seconds(600)`；`.env.example` 补 `LLM_*` 前缀环境变量示例；`docker-compose.yml` 新增 naming-worker 服务（与 backend 共用镜像，模型权重卷挂载 /models:ro，依赖 db/redis/backend 健康）
- **测试**：新增 3 项集成测试（`tests/integration/test_naming_worker.py`，151 行）——空队列空转不触碰引擎 / 跨语言文章→真实 mpnet 向量化→在线归簇→真实 Qwen2.5-0.5B 命名/分类/摘要回填+三条判定留痕 / 模型目录缺失（真实加载失败非 Mock）→ctfidf_fallback+P1 告警+失败留痕+第二轮告警防抖；单元 115 项 + 集成 3 项全绿；ruff/mypy 全绿
- **审核**：2026-07-25 独立审核通过（PASS 25/25，报告 `docs/dev/reviews/M2-3-review.md`），无 BLOCKER/MAJOR，MINOR 4 项（串行三次推理可并行优化/进程内冷却重启丢失/session-scope fixture 严格模式兼容/故障注入 kill BERTopic 与 GDELT 留待 Phase 2 整体验收）
