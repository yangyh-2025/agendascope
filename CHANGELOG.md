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
