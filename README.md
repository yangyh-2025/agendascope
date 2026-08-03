<div align="center">

<img src="frontend/public/logo.svg" alt="AgendaScope 观澜 Logo" width="96" height="96">

# AgendaScope 观澜 · 全球议程设置监控平台

**面向国家安全与国际关系研究机构的全球主流媒体舆情实时监控与议程设置识别系统**

**国际关系学院 · 国家安全计算模拟实验室**

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](backend/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](backend/app/main.py)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](frontend/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](deploy/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)](deploy/)
[![Elasticsearch](https://img.shields.io/badge/Elasticsearch-8-005571?logo=elasticsearch&logoColor=white)](deploy/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](deploy/)
[![AI](https://img.shields.io/badge/LLM%20%2B%20Embedding-Cloud%20API-7C3AED)](#llm-服务backendappllmm2-3)

</div>

平台持续监控 **108 个主要经济体与全球南方代表性国家**的主流新闻媒体（每国合计受众覆盖率 ≥70% 口径），实时识别"议程设置"行为：某国媒体、官员或重要人士率先提出议题，多国媒体随后跟随报道——平台自动判定**首发源、跟随国序列与时滞**，呈现完整传播链路，并随证据积累自我纠错（议题演化、次日归并、首发判定自动修正并全程留痕）。

## ✨ 特性

| 能力 | 说明 |
| --- | --- |
| 🗺️ 全球议程地图 | 108 国×今日报道量热力分布，点击国家下钻 Top 议题 |
| 🔥 热点议题排行 | 全球/按国显著性 TOP 10，一眼掌握当日议程 |
| 📡 实时采集 | 重点源 RSS 高频轮询 + GDELT 兜底，发布到可见 P95 ≤30 分钟 |
| 🔗 议程溯源 | 回声消除折叠多国跟风报道，识别首发源与跨国传播链路 |
| 🧠 多语言 AI 分析 | 跨语言向量聚类 + 云端大模型命名/分类/摘要/终审 |
| 🔁 自我纠错 | 议题生命周期管理、次日归并、判定修正留痕、人工否决优先 |
| 👤 人物/机构监测 | 关键人物、智库、国际组织首发信号跟踪 |
| 🚨 智能预警 | 规则评估、订阅推送、报告导出 |
| 📦 私有化部署 | Docker Compose 单机交付，LLM/嵌入走经批准的云通道 |

## 文档

- 产品文档：[`docs/pm/`](docs/pm/)（产品全景、需求分析、PRD、运营计划）
- 工程文档：[`docs/dev/`](docs/dev/)（技术方案、详细设计、架构决策、开发计划）
- 调研报告：[`docs/pm/_research/`](docs/pm/_research/)

## 仓库结构

```
├── backend/            # 后端服务（Python / FastAPI）
├── frontend/           # 前端看板（React / TypeScript / ECharts）
├── deploy/             # Docker Compose 与部署脚本
├── docs/               # 产品与工程文档
└── tests/              # 端到端与回放测试
```

## 快速开始

全 5 阶段开发完成（`v1.0.0`），性能与体验优化至 `v1.1.0`。详见 [`CHANGELOG.md`](CHANGELOG.md)。

### 后端（backend/）

Python 3.11 + FastAPI。配置项集中在 `backend/app/config.py`（`.env` 注入，模板见 `backend/.env.example`）。

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r backend/requirements.txt
docker compose -f deploy/docker-compose.yml up -d db redis elasticsearch rsshub  # 基础设施
cd backend && alembic upgrade head                                             # 建表（14 张核心表）
cd .. && .venv/Scripts/python.exe scripts/seed_sources.py                      # 种子源（108 国 124 源）+ 初始管理员
cd backend && uvicorn app.main:app --port 8000                                 # API 服务
python -m app.collector.worker                                                 # 采集调度 worker（另开终端）
python -m app.worker.nlp_worker                                                # NLP worker：语言识别→向量化→ES 同步→延迟埋点（另开终端）
python -m app.worker.cluster_worker                                            # 聚类 worker：在线归簇（消费 nlp:embedded）+ 每小时重聚类校正（另开终端）
python -m app.worker.naming_worker                                             # 命名 worker：待命名议题 → LLM 命名/分类/摘要回填（另开终端）
python -m app.worker.agenda_worker                                             # 议程引擎 worker：次日归并/消亡扫描/实体黑名单周期任务（另开终端）
python -m app.worker.snapshot_worker                                           # 快照 worker：国家×议题显著性 15min 刷新（另开终端）
python -m app.worker.detection_worker                                          # 事件检测 worker：活跃议题全链路检测（首发锚点/跟随序列/统计佐证/事件判定/终审，另开终端）
python -m app.worker.alerting_worker                                           # 预警调度 worker：规则评估/通知退避重试/订阅推送/报告导出队列（另开终端）
```

本地模型权重（不入库，放仓库根 `models/`）：仅 fastText `lid.176.bin`（语言识别，无 API 替代）；LLM 与嵌入走云端 API 无需本地权重。嵌入（SiliconFlow bge-m3，1024 维）与 LLM（讯飞星辰 MaaS）经 `LLM_` / `NLP_EMBEDDING_` 前缀环境变量配置（参考 `backend/.env.example`，`.env` 放仓库根、已 gitignore）。

聚类引擎（`backend/app/clustering/`，`CLUSTER_` 前缀环境变量可配）：BERTopic（UMAP+HDBSCAN+c-TF-IDF）主线 + Agglomerative 硬阈值（cosine 0.25，average linkage）双策略并行评估，单簇占比 >80% 触发超大簇黑洞护栏回落 Agglomerative；在线增量双阈值归簇（T_event=0.85 归簇 / T_dup=0.95 判重），孤证保留为 size=1 nascent 微簇；每小时全局重聚类校正（近 24h 窗）+ Redis 快照发布（校正期间读侧读上一版并标注"校正中"）；双策略均不可用时关键词匹配粗聚类降级（cluster_method=keyword_fallback + P1 告警 + 恢复后回填）。议题命名/分类/摘要由 LLM 服务经 `app.clustering.service.ClusterService` 接口接线。

质量门禁（仓库根执行，配置在 `pyproject.toml`）：

```bash
.venv/Scripts/python.exe -m ruff check backend tests scripts   # lint（E/F/W/I/UP/B/SIM/RET/C4）
.venv/Scripts/python.exe -m mypy                               # 类型检查（backend/app 全量）
.venv/Scripts/python.exe -m pytest tests -q                    # 单元 + 集成测试（集成需基础设施在线）
```

部署：`docker compose -f deploy/docker-compose.yml up -d` 起全栈（db/redis/es/rsshub/backend/worker/nlp-worker/cluster-worker/naming-worker/agenda-worker/snapshot-worker/detection-worker/alerting-worker），backend 容器启动时自动执行迁移。受限网络构建：`docker compose -f deploy/docker-compose.yml build --build-arg HTTPS_PROXY=http://host.docker.internal:11304 --build-arg APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn backend`。

### LLM 服务（backend/app/llm/，M2-3）

LLM 推理全部走云端 API（默认 `LLM_PROFILE=api`，OpenAI 兼容 `/chat/completions`），负责议题命名/分类/摘要、首发表述判定、终审审查官。默认接入讯飞星辰 MaaS（模型 `xophunyuan7bmt`，`GET /v2/models` 可查真实模型 ID）。本地 Qwen 推理分支（`backend/app/llm/engine.py` 的 `LLMEngine`）保留代码但不默认使用，仅作机构不批准云通道时的降级兜底。

关键环境变量（`.env` 放仓库根，已 gitignore）：

```bash
LLM_PROFILE=api
LLM_API_BASE_URL=https://maas-api.cn-huabei-1.xf-yun.com/v2   # OpenAI 兼容端点
LLM_API_KEY=xxxxxxxx:xxxxxxxx                                  # 供应商 API Key
LLM_API_MODEL=xophunyuan7bmt                                   # 模型 ID（GET /v2/models 查询）
LLM_MAX_CONCURRENCY=2                                          # API 并发上限（信号量，对齐供应商 QPS/并发配额）
```

其他可配项：`LLM_MAX_CONTEXT_TOKENS`（命名 prompt 预算，默认 2000）、`LLM_CATEGORIES`（JSON 数组，扩展主题分类体系）、`LLM_FAILURE_RATE_THRESHOLD`（降级判定阈值，默认 0.2）。参考 `backend/.env.example`（不含真实 Key）。

设计与行为要点：

- **结构化输出**：prompt 内嵌 JSON Schema 强约束 + pydantic 校验，解析失败重试 1 次后单点降级（未引入 outlines/约束解码：输出 schema 极简，理由见 `app/llm/schemas.py`）
- **异步批处理**：`LLMTaskQueue`（asyncio 队列 + 小窗口聚批 + 独立线程推理），主链路投递即返回，不阻塞采集
- **并发限制**：`LLM_MAX_CONCURRENCY` 线程信号量，跨命名/检测/终审/首发表述统一限流，对齐外部 API QPS/并发配额
- **降级链**：调用失败/超时率 >20%（滑窗）或 API 不可用 → c-TF-IDF 关键词标签兜底（`naming_method=ctfidf_fallback`，分类归「其他」，摘要留空不伪造）+ alerts 表 P1 告警（1h 防抖）+ WARN 日志；恢复后 `backfill_degraded_topics()` 对降级期议题回填重命名/分类/摘要并写 revision_log
- **prompt 版本管理**：命名/分类/摘要模板带版本号（`app/llm/prompts.py` 注册表，只增不改）；每次判定写 `llm_judgements` 表（模型名 + prompt_version + 输入/输出快照 + 耗时），topics 表冗余 `llm_model`/`prompt_version` 列；`rerun_judgements()` 支持换 prompt 后历史判定批量重跑对比
- **聚类管线接线**：`python -m app.worker.naming_worker` 轮询聚类侧待命名队列（`ClusterService.list_pending_naming`：在线归簇新建微簇与重聚类校正产出的兜底命名议题），经 `LLMTaskQueue` 投递 `TopicAnnotator` 组合标注（命名+分类+摘要），`record_llm_naming` 回填 topics；降级时议题落「关键词：」兜底标签保持 `ctfidf_fallback` 留痕 + P1 告警，worker 每轮做恢复探针（真实推理验证），恢复后自动 `backfill_degraded_topics` 回填降级期议题；单点降级议题 10 分钟重试冷却，不刷判定留痕

### 议程引擎（backend/app/agenda_engine/，M3-1）

回声消除 + 议题生命周期 + 次日归并 + 分裂回滚 + 动态实体黑名单；自我纠错核心（ADR-006），全部产出真实落库且 revision_log 留痕。`AGENDA_` 前缀环境变量可配（`backend/app/agenda_engine/config.py`）。

- **回声消除折叠**（`echo.py`）：同日 cosine ≥0.65 / 3 日内 ≥0.85 折叠为同一议题节点，全部来源保留 related_docs，canonical 永远是最早 TIME_PUB，时间衰减加权质心
- **议题生命周期状态机**（`lifecycle.py`）：nascent/forming/confirmed/evolving/archived 五态完整版；连续 7 天无新报道自动归档（保留可查）；人工锁定字段议题不自动消亡
- **次日自动归并**（`merge.py`）：candidate nascent 微簇 vs 历史活跃议题跨语言向量比对 ≥0.85 并入旧议题，topic_id 复用 + 推进 lifecycle_state；加载 no_merge_with 名单先行排除；人工锁定 'merged_into' 字段的源议题不自动归并
- **议题分裂与误并回滚**（`split.py` + `POST /api/v1/topics/{parent_id}/split`）：恢复双方 topic_id 与文章归属；双方写入 no_merge_with 防再误并；revision_log(actor='human', trigger='manual_split')；audit_logs 留痕；质心按剩余文章重算（time_decay_pool 不可逆）
- **动态高频实体黑名单**（`entity_blacklist.py` + `entity_extract.py`）：jieba 中文 NER + 英文大写规则，近 30 天 Top-50 实体写 Redis Set `entity:blacklist` TTL 48h；聚类/归并比对前过滤；刷新失败保旧值不抛错（优化非正确性依赖）
- **agenda worker**（`python -m app.worker.agenda_worker`）：归并（默认 60min）/消亡扫描（默认 60min）/黑名单刷新（默认 24h）三任务独立调度，启动即首轮全触发；单任务失败不阻塞其他任务

#### M3-2 首发源判定与传播链路（已交付，标签 `v0.3.1-m3-2`）

- **媒体首发锚点判定**（`origin.py` `detect_media_origin`）：议题簇内最早 published_at UTC；同秒并列通讯社原文优先（media_type/agency/wire 双通道）；time_source='crawled' 低置信"首发源待核实"不自动告警
- **persons_orgs 实体库与 NER**（`entity_repo.py`）：别名表精确匹配；同名歧义 confidence 衰减 + needs_review 进人工队列；与 T3.5 实体黑名单联动降权
- **LLM 首发表述判定器**（`first_utterance.py` + prompt `first-utterance-v1`）：候选全文+历史表述 ≤4000 token；evidence_quote 强制原文子串；无依据判定丢弃进人工队列；不可用回落 media_time_fallback；llm_judgements 留痕
- **跟随国序列计算**（`origin.py` `compute_follower_sequence`）：各国首篇 lag_hours 升序；14 天窗口；仅原创节点
- **统计佐证计算**（`stats_evidence.py`）：XCorr lag 0-14 + Granger + QAP；样本量 <100 硬性拒绝"数据量不足"；降级不抛异常

#### M3-3 事件判定与自我纠错（已交付，标签 `v0.3.2-m3-3`）

- **AgendaEvent 状态机**（`event.py`）：watching/suspected/confirmed/dismissed/revised/archived 六态；判定条件 a-d（首发源明确 + ≥3 国 14 天内跟随 + 统计显著 + 议题活跃）；upsert 不重置已 confirmed/archived 事件（人工结论机器不推翻）
- **LLM 终审审查官**（`final_review.py` + prompt `final-review-v1`）：对 suspected 事件评逻辑连贯性 1-10 分；<5 自动降 watching 不自动告警；≥5 维持；不可用跳过直进人工复核队列；final_review 字段（score/verdict/model/prompt_version/reasoning/concerns）留痕
- **增量重估与 revision_log**（`revision.py`）：新证据（更早报道/LLM 人物首发/统计变化）触发自动重跑首发源判定；判定变化字段逐个 append_revision（actor='machine'，含 model/prompt_version/trigger_evidence）；status='revised'；human_locked_fields 中的字段机器不推翻
- **置信度自动升降**（`confidence.py`）：watching→suspected 满足条件升级（origin_type 确定 + origin_confidence ∈ medium/high + 跟随国 ≥1 + 降级时统计显著）；origin_confidence 降 'low' 或 follower 清空撤销回 watching；修正风暴保护（24h 修正 >5 次冻结自动修正转人工）
- **人工确认/否决优先 API**（`api/routes/agenda_events.py`）：`POST /agenda-events/{id}/confirm` 人工确认升 confirmed（audit_logs 留痕）；`POST /agenda-events/{id}/revisions/{seq}/reject` 人工否决回滚到修正前值 + 新 revision 条目 + human_locked_fields 增加；`GET /agenda-events/{id}/revisions` 列出修正历史（rejected 标记）
- **AgendaSnapshot 快照引擎**（`snapshot.py` + `python -m app.worker.snapshot_worker`）：每 15 min 刷新国家×议题显著性得分/排名/top_attributes/network_metrics；单次 ≤5 min 超时保留上版；连续 3 次失败写 alerts P1 告警；UPSERT 幂等（UK country×topic×window×granularity）；sentiment 留 NULL 不伪造（Phase 4 情感分析接入后回填）

### 前端（frontend/）

React 18 + TypeScript + Vite。深色红蓝主题，design token 集中在 `frontend/src/theme/tokens.ts`。

```bash
cd frontend
npm install        # 已配置 npmmirror registry（frontend/.npmrc）
npm run dev        # 开发服务器 http://localhost:5173，/api 代理至 http://localhost:8000
npm run build      # 类型检查 + 产物构建（dist/）
npm run test       # Vitest 组件级测试
npm run lint       # ESLint
```

前置条件：后端服务已启动（`http://localhost:8000`）且已执行种子脚本（`scripts/seed_sources.py`）创建管理员账号。

功能（Phase 1）：

- 登录页：账号密码登录（`POST /api/v1/auth/login`），失败展示后端 message，未登录访问受保护路由自动跳登录页
- 主界面：左侧导航（看板/媒体源/系统），顶栏显示当前用户（`GET /api/v1/auth/me`）与退出登录
- 看板：基于 `GET /api/v1/sources` 实时数据按国家聚合的媒体源覆盖总览卡片
- 媒体源管理：源列表（名称/国家/类型/健康状态/最近采集时间，分页）；自助配源——粘贴 URL 试运行（`POST /api/v1/sources/crawl-preview`）核对样例后确认入库（`POST /api/v1/sources`，需 admin 角色）
- 会话：access_token 过期自动拦截 401 → `POST /api/v1/auth/refresh`（单飞，防并发重复刷新）→ 重试原请求；刷新失败清空会话并跳登录页

## 许可

本作品采用 [知识共享 署名-非商业性使用 4.0 国际 (CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/deed.zh-Hans) 协议进行许可:**学术/研究/个人可自由使用,商用需单独授权**(联系 yangyuhang2667@163.com)。

学术引用格式:

```
杨昱航. AgendaScope 观澜: 全球议程设置监控平台
[Computer software]. 2026.
https://github.com/yangyh-2025/agendascope
```

详见 [LICENSE](LICENSE)。
