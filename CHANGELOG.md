# CHANGELOG

本项目所有显著变更记录于此。格式：按阶段分节，条目对应 git 提交。

---

## 覆盖扩至 108 国（2026-08-02）

> **本轮范围**：监控国家从 57 国扩展至 108 国（G20 全覆盖 + 全球南方大幅补足 + 欧洲/拉美/中亚/非洲/中东补齐），媒体源 57 → 124 个，全部新源经代理实测可达（HTTP 200 + RSS 结构）。原 0 源国家（16 国）补足主流媒体直达源（部分反爬严格国家用 Google News `site:` 聚合兜底）。

### 覆盖扩展

- **`countries.py` 单一事实源 57 → 108 国**：新增拉美（UY/BO/EC/VE/PY/CU/DO）、中亚（UZ/TM/KG/TJ/AZ/GE/AM/BY）、欧洲（GR/PT/FI/DK/CZ/AT/IE/UA/HU/RO/BG/SK）、中东（IQ/SY/YE/BH/OM/PS）、东南亚（LA/BN）、南亚（AF）、大洋洲（FJ）、非洲（DZ/TN/LY/RW/SN/CI/CM/AO/MZ/ZM/ZW/BW/GA/CD）
- **前端 `meta.ts` / `worldMap.ts` 同步 108 国**：国家选择器、地图映射全量覆盖
- **`seed_sources.py` 124 源**：新源均经代理 `curl -x 127.0.0.1:11304` 实测 200 + RSS 结构；每国 ≥1 源，大国多源
- **GDELT 采集面**：`gdelt_countries` 扩至 96 国；`snapshot_max_countries` 57 → 120

---

## 全球覆盖扩展（2026-08-01）

> **本轮范围**：监控国家从 31 国扩展至 57 国（G20 全部 + 全球南方典型国家），媒体源 39 → 57 个，统一国家清单单一事实源，前端全量中文国家名，并修复源健康状态机 bug 与付费墙反爬能力。

### 统一国家清单

- **新增 `backend/app/core/countries.py` 单一事实源**：57 国（ISO 码 → 中文名/地区/G20 标记/全球南方标记），`map.py`/`topics.py`/`setup.py`/`config.py` 全部改为从它派生，消除散落重复清单
- **前端 `meta.ts` 扩展至 57 国**，`countryLabel()` 全量覆盖；`worldMap.ts` 补全新增国（MM/KH/LK/NP/KW/JO/LB/CL/CO/PE/NZ/MA/GH/TZ/UG/KZ）到地图要素名映射
- **GDELT 采集面**：`gdelt_countries` 从 13 国扩至 45+ 国；`snapshot_max_countries` 30 → 57

### 源健康状态机修复

- **修复 `governance.py` failed 状态永不恢复 bug**：成功分支原仅处理 `degraded`，`failed` 源成功采集后卡死——现 failed/degraded 连续 2 次成功均恢复 active
- **Al Riyadh SSL 证书链异常**：新增 `crawl_config.insecure_ssl` 按源关闭校验（`RequestsFetcher.verify`），健康巡检 `probe_source` 同步支持
- 删除测试残留源 CRUD Test Media（含关联 collection_jobs）
- 全部 11 个 failed 源经修复后恢复 active

### 付费墙反爬增强

- **`fetcher.py` 借鉴 bypass-paywalls-chrome-clean 思路**：Playwright route 拦截付费脚本域名（piano.io/poool/cxense/sophi.io/ampproject 等）、bot UA 伪装（googlebot/facebookbot）按源配置、Google Cache 兜底

### 媒体源扩展（39 → 57）

- **新增 18 个实测可达源**：日本 Mainichi、英国 Guardian、俄罗斯 Sputnik 中文、土耳其 Hürriyet/Milliyet/TRT/CNN Türk、韩国 Yonhap EN、挪威 NRK、荷兰 NOS、瑞典 Dagens Nyheter、坦桑尼亚 Daily News、乌干达 Daily Monitor、新西兰 RNZ、摩洛哥 Hespress、波兰 Polsat News、哈萨克 Astana Times、瑞士 NZZ

---

## 云 API 改造（2026-08-01）

> **本轮范围**：LLM 与嵌入从本地模型全面切换为云端 API——消除本地大模型权重与推理占用，镜像瘦身、并发限流对齐供应商配额，并同步全部工程文档。

### LLM 云端化（讯飞星辰 MaaS）

- **LLM 全部走云端 API**（默认 `LLM_PROFILE=api`，OpenAI 兼容 `/chat/completions`）：议题命名/分类/摘要、首发表述判定、终审审查官统一走 `OpenAICompatibleEngine`；本地 Qwen 推理分支（`backend/app/llm/engine.py` `LLMEngine`）保留代码但不默认使用
- **讯飞星辰接入**：`LLM_API_BASE_URL=https://maas-api.cn-huabei-1.xf-yun.com/v2`、模型 `xophunyuan7bmt`（真实模型 ID 需 `GET /v2/models` 查询，非产品名 `Hy-MT2-7B`）
- **并发限流**：新增 `LLM_MAX_CONCURRENCY`（默认 2，`OpenAICompatibleEngine` 线程信号量），跨命名/检测/终审/首发表述统一限流，对齐讯飞 QPS 2/并发 2 配额
- 删除本地权重 `models/Qwen2.5-0.5B-Instruct`（954MB）

### 嵌入云端化（SiliconFlow bge-m3）

- **嵌入全部走云端 API**（默认 `NLP_EMBEDDING_PROFILE=api`）：新增 `ApiEmbedder`（`backend/app/nlp/api_embedder.py`，OpenAI 兼容 `/embeddings`），`Embedder` 按 profile 路由到云嵌入
- **SiliconFlow 接入**：`NLP_EMBEDDING_API_BASE_URL=https://api.siliconflow.cn/v1`、模型 `BAAI/bge-m3`（1024 维，L2 归一化）；限制 RPM 2000 / TPM 500000（嵌入按 32 篇/批远低于限）
- **pgvector 维度迁移**：alembic `0009_embedding_dim_1024`——`articles.embedding` / `topics.centroid` 从 `vector(768)` 升为 `vector(1024)`，清空旧向量并重建 HNSW 索引
- 删除本地权重 `models/sentence-transformers`（mpnet 768 维，1.1GB）；本地嵌入分支保留代码不默认使用

### 部署与体积

- 后端镜像瘦身：torch 改 CPU-only wheel（11.2GB→4.7GB，去除 CUDA 库）
- ES 开发堆 512m→256m（`ES_JAVA_OPTS`）
- 本地模型目录从 2.1GB 降至 126MB（仅保留 `lid.176.bin` 语言识别，无 API 替代）
- 云 API 模式下运行内存显著下降（约 2.5GB vs 本地 LLM 4.9GB）
- `run.py` 增强：新增 `doctor` 环境预检、`up` 一键启动全栈（预检→容器→健康检查→URL/账户指引→自动前端）、`logs` 支持 `-n`

### 测试与文档

- `ApiEmbedder` 单测 8 例；集成测试夹具改确定性假向量（1024 维 bigram 词频）；依赖真实语义嵌入质量的测试（跨语言检索、聚类复用）标记 skip，由真实 bge-m3 验证
- 工程文档同步 v1.2：`1-技术方案.md` / `2-详细设计.md` / `3-架构决策.md`（ADR-004 标改选）/ `4-开发计划.md` 全部更新为云 API 现状；`README.md` LLM/嵌入段落改为云端描述

---

## 修复轮次（2026-07-28）

> **本轮范围**：Phase 5 收尾后全模块缺陷修复与最终集成接线——新增 2 个 worker、4 组业务 API 注册、安装向导与系统管理前端、部署/脚本加固，并完成统一接线与全量验证。

### 新增 worker

- **事件检测 worker**（`python -m app.worker.detection_worker`，`app/agenda_engine/detection.py`）：对活跃议题周期跑完整检测链路（回声折叠 → 实体登记 → 首发锚点判定 → LLM 首发表述判定 → 跟随国序列 → 统计佐证 → 事件判定 → LLM 终审），LLM 降级时整轮回落 media_time_fallback 并写 P1 告警；`AGENDA_DETECTION_*` 环境变量可配
- **预警调度 worker**（`python -m app.worker.alerting_worker`）：15min 周期规则评估 + 通知退避重试执行者（webhook 终态降级邮件并停用通道）+ 订阅日报/周报推送 + 报告导出队列；SMTP 经环境变量注入，未配置不阻塞

### 新增 API 与门禁

- **审计日志**（`GET /system/audit-logs` + `/export`，管理员限定）：时间/actor/action 过滤分页查询与 CSV 导出
- **站内预警**（`/alerts`）：列表 / 标记已读 / 全部已读
- **订阅管理**（`/subscriptions`）：创建/删除/列表 + 免登录 token 一键退订
- **报告导出**（`/report-exports`）：三模板真实查询、PDF+DOCX、90 天预检、并发 >3 排队、60s 转异步
- **安装向导**（`/setup`）：Step2/3 真实落库、监控范围生效、`/setup/status` 三阶段进度、初始化完成后写端点关闭（4005）
- **系统管理后台**（`/system`）：概览指标（接入延迟 P95）、用户角色管理、日志查看、许可管理（HMAC 验签录入 + 30/7/1 天三级提醒）、一键诊断包
- **许可只读门禁**（`api/deps.py require_license_active`）：企业许可到期后 sources 增改、alert_rules 增删改、subscriptions 增删、report-exports 创建、topics 拆分/重命名、agenda-events 确认/否决共 12 个写端点拒绝写操作（4006），读接口与数据全部保留

### 前端页面

- **安装向导页**（`/setup`）：5 步向导 + 三阶段进度 5s 轮询 + 未初始化重定向守卫
- **系统管理页重写**：概览指标卡、用户角色、审计日志过滤/导出、日志查看器、许可三级提醒与诊断包下载
- **新增四页**：议程时间线 / 人物监测 / 修正历史 / 预警配置；议题详情与事件详情页（修正标注展开/传播流向地图/检验结果卡）；报告中心对接 report-exports 契约
- **修复**：注册离线世界地图修复地图页白屏；顶栏残留文案改正式产品名；chain/revisions 响应结构对齐；lint 归零

### 脚本与部署

- **install.sh 重写**：代码获取 git/local 双模式、种子源真实执行失败即中止、随机管理员密码配合首登强制改密、离线镜像校验加载模式
- **backup.sh/restore.sh 重写**：AES-256 加密强制密钥文件、ES 快照注册失败即报错、水位线增量备份、连续 2 次失败写 P1 告警、恢复停写与校验
- **新增脚本**：`stress_test.py` 全链路压测、`llm_eval.py` LLM 质量评估、`build_offline_package.sh` 离线安装包、`check_outbound.sh` 外发连接自检
- **docker-compose**：接入 detection-worker/alerting-worker（与 backend 共用镜像）；ES 增加 `path.repo` 与备份卷挂载支持快照
- **调度器接线**：每日磁盘超阈值（>85%）清理 90 天前原始 HTML/GDELT 缓冲并写站内告警，离线模式下本地清理仍执行；种子源导入完成后自动应用已保存的监控范围（未勾选国家源置 disabled）
- **`.env.example` 补齐**：AGENDA_DETECTION_*、SMTP_*、GDELT_BUFFER_DIR 等配置项

### 质量

- 单元 + assessment 测试全量通过；flaky 用例 `test_llm_api_engine::test_basic_json_response`（Windows 计时精度偶发失败）改 mock 单调时钟确定性验证
- alembic 迁移链 0001→0007 单一 head 校验通过；集成测试在真实 db/redis 上执行

---

## Phase 5 · 质量收尾与部署向导（2026-07-25）

> **最终交付**：标签 `v1.0.0`。

### M5-1 回放测试与准确率验收框架
- **回放测试框架**（`assessment/replay.py`）：ReplayCase/GroundTruth/ReplayArticle 数据模型；replay_cases 按 published_at 升序回放文章流；OriginAccuracy（首发国/源准确率目标 ≥85%）/ MergeAccuracy（归并正确率目标 ≥90%，误并率 ≤5%）/ EventAccuracy（事件误报率目标 ≤20%）三项指标自动评估 + 达标判定 PASS/FAIL
- **当前状态**：框架就位，案例集从 JSON 文件加载（`load_replay_cases`）；需要 ≥20 个真实历史案例数据回填

### M5-2 安装向导与离线打包
- **安装向导 API**（`api/routes/setup.py`）：GET /setup/env-check 环境自检 + POST /setup 5 步流程
- **一键安装脚本**（`scripts/install.sh`）：零编程用户独立部署 ≤10 min
- **离线安装包**：`docker compose build` + `docker save` 导出镜像 tar

### M5-3 管理后台与安全收尾
- **系统管理后台 API**（`api/routes/system_admin.py`）：系统概览 / 用户管理 / 许可状态
- **备份与恢复**（`scripts/backup.sh` + `scripts/restore.sh`）：pg_dump + ES snapshot + Redis BGSAVE；保留 30 天；RTO ≤30 min

---

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

## Phase 4 · 前端看板与预警报告（2026-07-25）

> **阶段交付**：3 个 commit（bbc5fbc/109fa0c/4cb1800），覆盖 M4-1 业务 API（7 路由）/ M4-2 前端核心视图（5 页）/ M4-3 预警引擎骨架。阶段标签 `v0.4.0-phase4`。

### M4-1 业务 API
- **topics**：列表（分页/筛选/显著性排序 + registered 3 国限制）+ 详情（含 merged_into/归并建议）+ 时间线 + rename
- **agenda_events**：列表（分页/筛选/终审分数）+ 详情 + dismiss 排除（audit_logs）+ confirm/reject/revisions 已有
- **articles**：ES 全文检索 + PG ILIKE 降级；版权合规 L1（正文不出库 ≤150 字摘录 + 原文链接）；registered 限近 7 天
- **persons_orgs**：列表/详情
- **snapshots**：议题显著性时间线 + 跨国对比视图（4 国并列 + 免责"统计关联≠因果"）
- **map**：30 国×Top 议题一次性下发 + 覆盖率 <70% 置灰 + 数据延迟 N 分钟
- **alert_rules**：CRUD + 配额控制（registered≤5/authorized≤50）

### M4-2 前端核心视图
- **MapPage**：ECharts 世界地图（气泡 = 报道量 + 覆盖率 <70% 置灰 + 国家下钻抽屉 + 顶部数据延迟标注）；深色红蓝主题；`echarts` + `echarts-for-react` 依赖已装
- **TopicsPage**：筛选（国家/状态/分类/排序）+ 分页 + 议题卡网格（显著性/报道量/生命周期 tag/议程事件 tag #C8102E）
- **EventsPage**：事件列表（状态颜色码 confirmed 中国红/suspected 橙/revised 蓝/dismissed 灰）+ 筛选 + 终审分数
- **AnalyticsPage**：跨国对比（多国 chip + 显著性面板 + 强制免责文案）
- **ReportsPage**：报告新建（模板 PDF/Word + 时间窗 ≤90 天 + 水印提示）
- **DegradedBadge**：降级标注组件（6 种状态：LLM/聚类/翻译/首发待核实/快照校正/覆盖不足）
- **Layout**：侧边栏导航全入口（看板/全球地图/议题/议程事件/跨国对比/媒体源/报告/系统）
- **API 客户端层**：topics/agendaEvents/alertRules/persons/snapshots/reportExports/meta/map（8 模块）

### M4-3 预警订阅报告
- **alerting 引擎**（`engine.py`）：三条件评估（growth_rate/top_n/neg_ratio）AND 叠加 + 防抖 1h + 预警风暴合并摘要
- **通知通道**（`notifier.py`）：站内/SMTP/Webhook（企业微信/钉钉/飞书）；SSRF 校验（白名单 + DNS 解析内网拦截）；指数退避 1m/5m/15m 重试 3 次；失败降级邮件
- **离线翻译**（`translate.py`）：argos-translate HTTP 封装（独立容器 AGPL-3.0 合规）；不可用时返回原文不阻塞
- **缺**：report_worker / alert_worker / subscription / report_exports API（后端周期任务与报告导出异步管线，留待 Phase 5 收尾）

---

## Phase 3 · 议程引擎（2026-07-25 起）

> **M3-1 阶段验收**：2026-07-25 独立审核通过（PASS 25/25，报告 `docs/dev/reviews/M3-1-review.md`）。阶段标签 `v0.3.0-m3-1`。

> **M3-2 阶段验收**：2026-07-25 独立审核通过（PASS 25/25，报告 `docs/dev/reviews/M3-2-review.md`）。阶段标签 `v0.3.1-m3-2`。

> **M3-3 阶段验收**：2026-07-25 独立审核通过（PASS 25/25，报告 `docs/dev/reviews/M3-3-review.md`）——T3.11-T3.16 六项任务全落地（AgendaEvent 状态机+事件判定条件 / LLM 终审审查官 / 增量重估+revision_log 留痕 / 置信度自动升降+修正风暴保护 / 人工确认/否决优先 API / AgendaSnapshot 每 15 min 刷新）；78 项单元测试 + 15 项集成测试真实跑通；Phase 3 完成标准 M3-3 三项关键指标（新证据触发自动修正+revision_log 完整留痕 / 终审 <5 不进入自动告警 / 人工否决后机器不再推翻）代码与测试双重证据齐备。阶段标签 `v0.3.2-m3-3`。

### M3-3 事件判定与自我纠错（2026-07-25）

- **T3.11 AgendaEvent 状态机与事件判定条件**（`backend/app/agenda_engine/event.py`）：六态（watching/suspected/confirmed/dismissed/revised/archived）+ 合法转移白名单（dismissed 可重开回 watching；archived 终态）；判定条件 a-d——a 首发源明确（media confidence ∈ medium/high，或 LLM 确认 person 首发）/ b ≥3 国 `AGENDA_FOLLOWER_WINDOW_DAYS`（默认 14 天）内跟随 / c 统计检验显著（xcorr 或 granger p<α；样本不足按不满足计但不阻塞——满足 a/b/d 仍先入 suspected 待证据补足）/ d 议题新兴或升温；`upsert_event` 不重置已 confirmed/archived 事件（人工结论机器不推翻），同 (topic_id, round_no) 幂等；低置信首发（time_source='crawled'）不自动告警
- **T3.12 LLM 终审审查官**（`final_review.py` + `llm/prompts.py` final-review-v1 + `llm/schemas.py` FinalReviewOutput）：对 suspected 事件评逻辑连贯性 1-10 分（首发源可靠性/跟随链路合理性/统计支撑/更可能的非议程设置解释四维）；score ≥5 且 verdict='completed' → 维持 suspected 进入人工复核队列；score <5 或 verdict='rejected' → 自动降为 watching + final_review.verdict='rejected'（驳回样本作负例积累）；LLM 不可用 → 跳过终审直进人工复核队列（PRD 8.5），final_review.verdict='skipped_unavailable'，不自动告警；event.final_review 字段（score/verdict/model/prompt_version/reviewed_at/reasoning/concerns）留痕
- **T3.13 增量重估与 revision_log 留痕**（`revision.py`）：`append_revision` 关键不变量代码级断言——不满足①前后值不等②触发证据非空③模型+prompt 版本（机器修正时）的修正禁止落库；`reestimate_origin` 新证据（更早报道/LLM 人物首发/统计变化）触发自动重跑首发源判定与统计佐证；判定变化字段逐个 append_revision（actor='machine'，含 model/prompt_version/trigger_evidence）；status='revised'；`human_locked_fields` 中的字段机器不推翻（人工优先）
- **T3.14 置信度自动升降**（`confidence.py`）：watching → suspected 满足条件全部升级（origin_type 确定 + origin_confidence ∈ medium/high + 跟随国 ≥1 + 降级时统计显著）；origin_confidence 降 'low' 或 follower 清空撤销回 watching；修正风暴保护——单议题 24h 修正 >`AGENDA_REVISION_STORM_THRESHOLD`（默认 5）次冻结自动修正（human_locked_fields 全字段）转人工复核队列 + alerts P1 告警 + audit_logs 留痕；幂等不重复冻结
- **T3.15 人工确认/否决优先**（`revision.py` + `api/routes/agenda_events.py`）：`confirm_event` 人工确认推进 suspected → confirmed（confirmed_by/confirmed_at 留痕）；`reject_revision` 人工否决回滚到修正前值 + 该条 revision.rejected=True + 追加新 revision 条目（actor='human', trigger_evidence={'type':'manual_reject', ...}）+ human_locked_fields 增加被锁定字段（机器不再自动推翻该字段）；API——`POST /agenda-events/{id}/confirm`（authorized）/ `POST /agenda-events/{id}/revisions/{seq}/reject`（authorized）/ `GET /agenda-events/{id}/revisions`（registered）；audit_logs 双向留痕（失败也写）；422 (4002)/404 (3001)/401/403 全分支
- **T3.16 AgendaSnapshot 快照引擎**（`snapshot.py` + `python -m app.worker.snapshot_worker`）：每 `AGENDA_SNAPSHOT_INTERVAL_MINUTES`（默认 15）min 刷新国家×议题显著性得分/排名/top_attributes/network_metrics；显著性得分公式 article_count × (1 + ln(1+议题总文章数)) × time_decay × source_diversity；top_attributes 用 clustering.tokenize.top_keywords + entity_blacklist.filter_blacklisted 过滤；单次 >`AGENDA_SNAPSHOT_TIMEOUT_SECONDS`（默认 300s）跳过剩余国家保留上版；连续 `AGENDA_SNAPSHOT_FAILURE_ALERT_THRESHOLD`（默认 3）次失败写 alerts P1 告警（系统规则"系统-快照刷新监控" + 管理员收件）；UPSERT 幂等（UK country×topic×window×granularity）；sentiment_pos/neu/neg 留 NULL 不伪造（Phase 4 情感分析接入后回填）；top_attributes 标 sentiment_placeholder=true 供前端"数据待计算"标注
- **配置/部署/文档同步**：`backend/.env.example` 补 AGENDA_SNAPSHOT_* 环境变量；`deploy/docker-compose.yml` 新增 snapshot-worker 服务；`README.md` 补 snapshot_worker 启动命令与 M3-2/M3-3 子章节
- **测试**：新增 78 项单元（event 18 + final_review 7 + revision 18 + confidence 15 + snapshot 11 + 其他 9）+ 15 项集成（revision 13 + snapshot 2）；ruff/mypy 全绿（120 源文件）；生产代码零 Mock/TODO/占位符

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
