<div align="center">

<img src="frontend/public/logo.svg" alt="AgendaScope 观澜 Logo" width="96" height="96">

# AgendaScope 观澜 · 全球议程设置监控平台

**面向国家安全与国际关系研究的全球主流媒体舆情实时监控与议程设置识别系统**

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](backend/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](backend/app/main.py)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](frontend/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](deploy/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](deploy/)
[![LLM](https://img.shields.io/badge/LLM-Cloud%20API-7C3AED)](#llm-服务)

</div>

平台持续监控 **172 个国家** 的 **408 个主流媒体源**，实时识别"议程设置"行为：某国媒体、官员或重要人士率先提出议题，多国媒体随后跟随报道——平台自动判定**首发源、跟随国序列与时滞**，呈现完整传播链路，并随证据积累自我纠错（议题演化、次日归并、首发判定自动修正并全程留痕）。

## 🌟 核心能力

| 能力 | 说明 |
| --- | --- |
| 🗺️ **全球议程地图** | 172 国×今日报道量热力分布，点击国家下钻 Top 议题 |
| 🔥 **热点议题排行** | 全球/按国显著性 TOP 10，跨语言向量聚类 |
| 📡 **实时采集** | 408 个媒体源 RSS 高频轮询，发布到可见 ≤15 分钟 |
| 🔗 **议程溯源** | 回声消除折叠多国跟风报道，识别首发源与跨国传播链路 |
| 🕸️ **监控对象** | 50 精品关键人物/机构社交图谱，**每条关系边都有新闻证据支撑**（点边查看原文） |
| 🚨 **智能预警** | 规则评估 + 订阅推送 + LLM 中文摘要 |
| 🗄️ **结构化数据库** | L0-L3 四层架构 + article_processing 状态机 + 分布式任务队列（借鉴 GDELT 但超越） |
| 🔑 **数据开放平台** | RESTful API + X-API-Key 鉴权 + Redis 限流（`/developer`） |
| 📦 **私有化部署** | Docker Compose 单机交付，LLM 走经批准的云通道 |

## 🏗️ v4.0 分布式架构

<div align="center">
<img src="docs/assets/architecture.svg" alt="AgendaScope v4.0 分布式架构" width="900">
</div>

**关键设计**：
- **数据库即事实源**：所有看板/议题/事件/监控对象/预警功能都是对数据库的 `SELECT` 查询
- **处理状态机**：每篇文章的 NLP/聚类/实体抽取/关系抽取状态显式落库（`article_processing`），失败可重跑
- **分布式任务队列**：`worker_tasks` 表 + `FOR UPDATE SKIP LOCKED`，本地机器随时接入
- **议题生命周期事件**：每次合并/重命名/首发修正都留 `topic_lifecycle_events` 表
- **实体-文章显式关联**：`article_entities` 表替代 JSONB，可索引可连接

## 🗄️ 数据库四层架构

| 层 | 表 | 职责 |
|----|----|------|
| **L0 原始层** | `articles` / `sources` / `collection_jobs` | 采集入库，原文+元数据 |
| **L1 加工层** | `article_processing` / `article_entities` / `worker_tasks` | 处理状态机 + 任务队列 |
| **L2 事实层** | `topics` / `agenda_events` / `persons_orgs` / `entity_relations` | 对外呈现的事实 |
| **L3 快照层** | `topic_snapshots` / `entity_snapshots` / `source_snapshots` | 预聚合时序数据，看板秒查 |

## 🚀 快速开始

### 仅使用云端服务（无需部署）

```bash
# 浏览：https://www.wordread.cn
# 数据 API：https://www.wordread.cn/developer

curl -H "X-API-Key: YOUR_KEY" \
  "https://www.wordread.cn/api/v1/open/topics?status=heating&page_size=10"
```

### 本地算力机接入（贡献采集/NLP 算力）

```bash
git clone https://github.com/yangyh-2025/agendascope.git
cd agendascope/local_workers
cp .env.example .env  # 填入云端 PG 连接串 + LLM API Key
docker compose --env-file .env up -d
```

详见 [local_workers/README.md](local_workers/README.md)。

### 完整私有化部署（云端）

```bash
cd deploy
cp .env.example .env  # 配置 LLM_API_KEY 等
docker compose --env-file .env -f docker-compose.yml -f compose.deploy.yml up -d
```

## 📚 文档

- [详细设计](docs/dev/2-详细设计.md)：数据库 schema、状态机、算法
- [分布式部署指南](docs/dev/分布式部署指南.md)：本地算力机接入步骤
- [算力机申请单](docs/apply/本地算力机申请单.md)：硬件配置申请模板
- [API 文档](https://www.wordread.cn/developer/docs)：数据开放平台在线文档
- [CHANGELOG](CHANGELOG.md)：版本演进历史

## 🛠️ 技术栈

- **后端**：Python 3.11 + FastAPI + SQLAlchemy 2 + Alembic + pgvector
- **前端**：React 18 + TypeScript + Vite + ECharts + Three.js
- **数据库**：PostgreSQL 16 + pgvector（1024 维 embedding）
- **缓存**：Redis 7（30s GET 缓存 + API Key 限流）
- **LLM**：GLM-4-Flash / SiliconFlow（多模型池 + 失败转移熔断）
- **Embedding**：bge-m3（1024 维跨语言）
- **采集**：RSS + 网页抓取 + GDELT 兜底
- **部署**：Docker Compose + nginx + HTTPS

## 📊 数据规模

- **172** 个国家（含全球南方代表性国家）
- **408** 个媒体源（报纸/通讯社/广播/网络）
- **~500** 篇/天 文章采集
- **50** 个精品监控对象（避开元首与中国相关实体）
- **16** 种封闭关系类型（meets/sanctions/appoints/...）

## 📖 论文引用

如果本项目对您的研究或教学有帮助，请按以下格式引用：

**APA**：
```
Yang, Y. (2026). AgendaScope: A Real-time Global Agenda-Setting Monitoring
Platform [Computer software]. GitHub. https://github.com/yangyh-2025/agendascope
```

**BibTeX**：
```bibtex
@software{yang2026agendascope,
  author = {Yang, Yuhang},
  title = {AgendaScope: A Real-time Global Agenda-Setting Monitoring Platform},
  year = {2026},
  publisher = {GitHub},
  url = {https://github.com/yangyh-2025/agendascope}
}
```

**GB/T 7714**：
```
杨昱航. AgendaScope：全球议程设置实时监控平台[CP/OL]. (2026-08-05).
https://github.com/yangyh-2025/agendascope.
```

## 🤝 贡献

本项目为学术研究项目，欢迎 Issue 与 Discussion。如需贡献代码，请先阅读 [详细设计](docs/dev/2-详细设计.md)。

## 📄 License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) — 仅限非商业学术用途

## 📧 联系

- **作者**：杨昱航
- **邮箱**：yangyuhang2667@163.com
- **GitHub Issues**：[yangyh-2025/agendascope/issues](https://github.com/yangyh-2025/agendascope/issues)
