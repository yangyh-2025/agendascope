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
```

质量门禁（仓库根执行，配置在 `pyproject.toml`）：

```bash
.venv/Scripts/python.exe -m ruff check backend tests scripts   # lint（E/F/W/I/UP/B/SIM/RET/C4）
.venv/Scripts/python.exe -m mypy                               # 类型检查（backend/app 全量）
.venv/Scripts/python.exe -m pytest tests -q                    # 单元 + 集成测试（集成需基础设施在线）
```

部署：`docker compose -f deploy/docker-compose.yml up -d` 起全栈（db/redis/es/rsshub/backend/worker），backend 容器启动时自动执行迁移。受限网络构建：`docker compose -f deploy/docker-compose.yml build --build-arg HTTPS_PROXY=http://host.docker.internal:11304 --build-arg APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn backend`。

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
