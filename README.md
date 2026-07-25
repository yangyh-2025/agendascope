# AgendaScope 观澜 · 全球议程设置监控平台

面向国家安全机关与国际关系、国际政治、国家安全研究机构的全球主流媒体舆情实时监控与议程设置识别系统。

平台持续监控约 30 个主要经济体与全球南方代表性国家的主流新闻媒体（每国合计受众覆盖率 ≥70% 口径），实时识别"议程设置"行为：某国媒体、官员或重要人士率先提出议题，多国媒体随后跟随报道——平台自动判定首发源、跟随国序列与时滞，呈现完整传播链路，并随证据积累自我纠错（议题演化、次日归并、首发判定自动修正并全程留痕）。

## 核心能力

- **实时监控**：重点源 RSS 高频轮询 + GDELT 兜底，新闻发布到平台可见 P95 ≤ 30 分钟（红线 ≤ 2 小时）
- **议程溯源**：回声消除折叠多国跟风报道，识别首发源与跨国传播链路，自动判定议程设置事件
- **自我纠错**：议题生命周期管理（萌芽→形成中→已确认→演化→消亡）、次日自动归并、判定修正留痕、人工否决优先
- **多语言分析**：跨语言向量聚类 + 本地大模型（议题命名/分类/摘要/终审），数据不出内网
- **小白可用**：零编程基础用户可用的可视化看板（全球议程地图、议程时间线、传播链路图）
- **私有化部署**：Docker Compose 单机部署，支持完全离线内网环境

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

开发中。各阶段完成后本章节将同步更新安装与运行说明，详见 [`CHANGELOG.md`](CHANGELOG.md)。

### 后端（backend/）

Python 3.11 + FastAPI。配置项集中在 `backend/app/config.py`（`.env` 注入，模板见 `backend/.env.example`）。

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r backend/requirements.txt
docker compose -f deploy/docker-compose.yml up -d db redis elasticsearch rsshub  # 基础设施
cd backend && alembic upgrade head                                             # 建表（14 张核心表）
cd .. && .venv/Scripts/python.exe scripts/seed_sources.py                      # 种子源（31 国 39 源）+ 初始管理员
cd backend && uvicorn app.main:app --port 8000                                 # API 服务
python -m app.collector.worker                                                 # 采集调度 worker（另开终端）
python -m app.worker.nlp_worker                                                # NLP worker：语言识别→向量化→ES 同步→延迟埋点（另开终端）
python -m app.worker.cluster_worker                                            # 聚类 worker：在线归簇（消费 nlp:embedded）+ 每小时重聚类校正（另开终端）
python -m app.worker.naming_worker                                             # 命名 worker：待命名议题 → LLM 命名/分类/摘要回填（另开终端）
```

NLP 模型权重（不入库，放仓库根 `models/`）：fastText `lid.176.bin` 与 `sentence-transformers/paraphrase-multilingual-mpnet-base-v2/`；路径与设备经 `NLP_` 前缀环境变量可配（`backend/app/nlp/config.py`，`NLP_DEVICE=cuda/auto` 启用 GPU）。

聚类引擎（`backend/app/clustering/`，`CLUSTER_` 前缀环境变量可配）：BERTopic（UMAP+HDBSCAN+c-TF-IDF）主线 + Agglomerative 硬阈值（cosine 0.25，average linkage）双策略并行评估，单簇占比 >80% 触发超大簇黑洞护栏回落 Agglomerative；在线增量双阈值归簇（T_event=0.85 归簇 / T_dup=0.95 判重），孤证保留为 size=1 nascent 微簇；每小时全局重聚类校正（近 24h 窗）+ Redis 快照发布（校正期间读侧读上一版并标注"校正中"）；双策略均不可用时关键词匹配粗聚类降级（cluster_method=keyword_fallback + P1 告警 + 恢复后回填）。议题命名/分类/摘要由 LLM 服务经 `app.clustering.service.ClusterService` 接口接线。

质量门禁（仓库根执行，配置在 `pyproject.toml`）：

```bash
.venv/Scripts/python.exe -m ruff check backend tests scripts   # lint（E/F/W/I/UP/B/SIM/RET/C4）
.venv/Scripts/python.exe -m mypy                               # 类型检查（backend/app 全量）
.venv/Scripts/python.exe -m pytest tests -q                    # 单元 + 集成测试（集成需基础设施在线）
```

部署：`docker compose -f deploy/docker-compose.yml up -d` 起全栈（db/redis/es/rsshub/backend/worker/nlp-worker/cluster-worker/naming-worker），backend 容器启动时自动执行迁移。受限网络构建：`docker compose -f deploy/docker-compose.yml build --build-arg HTTPS_PROXY=http://host.docker.internal:11304 --build-arg APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn backend`。

### LLM 服务（backend/app/llm/，M2-3）

本地大模型推理服务（Qwen 系列，transformers 运行时），负责议题命名/分类/摘要，数据不出内网。模型权重放根目录 `models/`（已 gitignore，部署时单独分发）。

配置档（环境变量 `LLM_PROFILE` 切换）：

| 配置档 | 硬件 | 推荐模型 | 单议题延迟目标（估算） |
|--------|------|----------|------------------------|
| `gpu-24g` | 1×24GB GPU | Qwen2.5-14B-Instruct-GPTQ-Int4（或 7B） | P95 ≤10s |
| `cpu-quant` | CPU 量化 | Qwen2.5-3B-Instruct（int8/GGUF 转换后落 models/） | P95 ≤60s |
| `cpu-dev`（默认） | CPU 开发/测试 | Qwen2.5-0.5B-Instruct（float32） | 实测见 CHANGELOG |

关键环境变量：`LLM_MODEL_DIR`（覆盖模型目录，相对仓库根）、`LLM_DEVICE`、`LLM_MAX_CONTEXT_TOKENS`（命名 prompt 预算，默认 2000）、`LLM_CATEGORIES`（JSON 数组，扩展主题分类体系）、`LLM_FAILURE_RATE_THRESHOLD`（降级判定阈值，默认 0.2）。

开发环境模型下载（权重不进 git）：

```bash
# 直连失败时先 export HF_ENDPOINT=https://hf-mirror.com
.venv/Scripts/python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen2.5-0.5B-Instruct', local_dir='models/Qwen2.5-0.5B-Instruct')"
```

设计与行为要点：

- **结构化输出**：prompt 内嵌 JSON Schema 强约束 + pydantic 校验，解析失败重试 1 次后单点降级（未引入 outlines/约束解码：输出 schema 极简，小模型 CPU 场景收益有限且增加版本耦合，理由见 `app/llm/schemas.py`）
- **异步批处理**：`LLMTaskQueue`（asyncio 队列 + 小窗口聚批 + 独立线程推理），主链路投递即返回，不阻塞采集
- **降级链**：推理失败/超时率 >20%（滑窗）或模型加载失败 → c-TF-IDF 关键词标签兜底（`naming_method=ctfidf_fallback`，分类归「其他」，摘要留空不伪造）+ alerts 表 P1 告警（1h 防抖）+ WARN 日志；恢复后 `backfill_degraded_topics()` 对降级期议题回填重命名/分类/摘要并写 revision_log
- **prompt 版本管理**：命名/分类/摘要模板带版本号（`app/llm/prompts.py` 注册表，只增不改）；每次判定写 `llm_judgements` 表（模型名 + prompt_version + 输入/输出快照 + 耗时），topics 表冗余 `llm_model`/`prompt_version` 列；`rerun_judgements()` 支持换 prompt 后历史判定批量重跑对比
- **聚类管线接线**：`python -m app.worker.naming_worker` 轮询聚类侧待命名队列（`ClusterService.list_pending_naming`：在线归簇新建微簇与重聚类校正产出的兜底命名议题），经 `LLMTaskQueue` 投递 `TopicAnnotator` 组合标注（命名+分类+摘要），`record_llm_naming` 回填 topics；降级时议题落「关键词：」兜底标签保持 `ctfidf_fallback` 留痕 + P1 告警，worker 每轮做恢复探针（真实推理验证），恢复后自动 `backfill_degraded_topics` 回填降级期议题；单点降级议题 10 分钟重试冷却，不刷判定留痕

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

私有软件，保留所有权利，详见 [LICENSE](LICENSE)。
