调研完成，以下为完整报告。

# 全球议程设置监控平台 —— 开源组件调研报告

> 数据时间：2026-07-24，star 数与维护状态均通过 GitHub API 实时核实。

## 1. 新闻抓取 / 正文解析

| 仓库 | Stars | 语言 | 许可证 | 说明 | 维护状态 |
|---|---|---|---|---|---|
| [codelucas/newspaper](https://github.com/codelucas/newspaper)（newspaper3k） | ~15.1k | Python | MIT | 经典新闻正文+元数据抽取库，自带 NLP 摘要/关键词 | 2020 年后长期停更（近期 push 多为 metadata），不建议直接用 |
| [AndyTheFactory/newspaper4k](https://github.com/AndyTheFactory/newspaper4k) | ~1.1k | Python | MIT | newspaper3k 的活跃 fork，修复依赖并加入基准测试 NAEB | ✅ 活跃（2026-07） |
| [fhamborg/news-please](https://github.com/fhamborg/news-please) | ~2.5k | Python | Apache-2.0 | 基于 Scrapy 的新闻爬虫框架，支持整站抓取与通用正文抽取，自带通用抽取器 | ✅ 活跃（2026-04） |
| [kurtmckee/feedparser](https://github.com/kurtmckee/feedparser) | ~2.4k | Python | BSD（实际） | RSS/Atom 解析事实标准，容错极强 | ✅ 活跃（2026-07） |
| [kotartemiy/newscatcher](https://github.com/kotartemiy/newscatcher) | ~3.0k | Python | MIT | 按媒体站点抓 RSS 头条，内置数千媒体源清单 | ❌ 停更（2020-10），但媒体源清单可参考 |
| [adbar/trafilatura](https://github.com/adbar/trafilatura) | ~6.3k | Python | Apache-2.0 | 网页正文抽取基准领跑者（多份评测 F1 最高），多语言、含元数据，HuggingFace/IBM 在用 | ✅ 非常活跃（2026-07） |
| [buriy/python-readability](https://github.com/buriy/python-readability)（readability-lxml） | ~2.9k | Python | Apache-2.0 | Mozilla Readability 的 Python 移植，阅读模式式正文抽取 | 低频维护（2026-01） |

实践参考：[Scraping Web Page Content with Trafilatura, Readability, Newspaper3k & Playwright](https://www.justtothepoint.com/code/scrape/) 给出的"逐级降级"策略（trafilatura → readability-lxml → newspaper → Playwright）值得借鉴；另见 [free-news-api/news-crawlers 对比](https://github.com/free-news-api/news-crawlers) 和 [Trafilatura vs Readability vs Newspaper4k](https://www.contextractor.com/trafilatura-vs-readability-vs-newspaper/)。

## 2. RSS 生态

| 仓库 | Stars | 语言 | 许可证 | 说明 | 维护状态 |
|---|---|---|---|---|---|
| [DIYgod/RSSHub](https://github.com/DIYgod/RSSHub) | ~45.4k | TypeScript | AGPL-3.0 | 万物皆可 RSS：为无数站点（含无 RSS 的各国媒体、社媒）生成订阅源 | ✅ 极活跃 |
| [DIYgod/RSSHub-Radar](https://github.com/DIYgod/RSSHub-Radar) | ~7.3k | TypeScript | AGPL-3.0 | 浏览器插件，自动发现页面可用 RSSHub 路由 | ✅ 活跃 |
| [FreshRSS/FreshRSS](https://github.com/FreshRSS/FreshRSS) | ~15.6k | PHP | AGPL-3.0 | 自托管 RSS 聚合器，多用户、可扩展 | ✅ 极活跃 |
| [miniflux/v2](https://github.com/miniflux/v2) | ~9.5k | Go | Apache-2.0 | 极简高性能 RSS 聚合器（Go + PostgreSQL），有干净的 API 可当采集器用 | ✅ 极活跃 |
| [AboutRSS/ALL-about-RSS](https://github.com/AboutRSS/ALL-about-RSS) | ~5.9k | — | CC-BY-4.0 | RSS 工具、服务、源列表的大全 | ✅ 活跃 |
| [plenaryapp/awesome-rss-feeds](https://github.com/plenaryapp/awesome-rss-feeds) | ~2.6k | — | CC0-1.0 | 按国家/类别整理的新闻媒体 RSS 源清单（OPML 可导入），是搭建"各国主流媒体"源库的直接素材 | ✅ 活跃 |

要点：对本平台而言，RSSHub（补源）+ 各国媒体 RSS 清单（种子库）+ feedparser（解析）即可覆盖大部分主流媒体的实时采集，无需大规模爬 HTML。

## 3. GDELT 客户端

| 仓库 | Stars | 语言 | 许可证 | 说明 | 维护状态 |
|---|---|---|---|---|---|
| [alex9smith/gdelt-doc-api](https://github.com/alex9smith/gdelt-doc-api) | ~225 | Python | MIT | GDELT 2.0 DOC API 的 Python 客户端，支持文章检索与 timeline 音量分析（滚动 3 个月窗口、65 语言） | 维护放缓（2025-04），但 API 稳定仍可用 |
| linwoodc3/gdelt-py | — | — | — | 曾经的 GDELT 1.x/2.x 客户端，**仓库已删除/404** | ❌ 不可用 |

GDELT DOC API 本身免费且无需 key（见 [官方介绍](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) 与 [API cheatsheet](https://github.com/nomyx-io/gdelt-search/blob/main/API.md)），即使客户端停更也可直接封装 REST 调用。GDELT 是全球议程监控最便宜的"免费数据源"，建议作为自采之外的补充信号。

## 4. 话题建模 / 聚类

| 仓库 | Stars | 语言 | 许可证 | 说明 | 维护状态 |
|---|---|---|---|---|---|
| [MaartenGr/BERTopic](https://github.com/MaartenGr/BERTopic) | ~7.8k | Python | MIT | Transformer embedding + UMAP + HDBSCAN + c-TF-IDF，原生支持 50+ 语言的 sentence-transformers 后端，有在线/增量模式 | ✅ 活跃 |
| [ddangelov/Top2Vec](https://github.com/ddangelov/Top2Vec) | ~3.1k | Python | BSD-3 | 自动发现话题数、语义搜索一体的老牌方案 | ⚠️ 基本停更（2024-11），且依赖 gensim 老版本 |
| [huggingface/sentence-transformers](https://github.com/huggingface/sentence-transformers)（原 UKPLab） | ~18.9k | Python | Apache-2.0 | 句向量框架，`paraphrase-multilingual-mpnet-base-v2` / `LaBSE` 等跨语言模型直接可用 | ✅ 极活跃 |
| [lmcinnes/umap](https://github.com/lmcinnes/umap) / [scikit-learn-contrib/hdbscan](https://github.com/scikit-learn-contrib/hdbscan) | ~8.2k / ~3.1k | Python | BSD-3 | BERTopic 的底层降维与聚类组件，可独立组装自定义管线 | ✅ 活跃 |

选型建议：BERTopic + 多语言 sentence-transformers 是目前"新闻话题聚类"的标准答案；跨语言场景用 `paraphrase-multilingual-*` 把所有语言映射到同一向量空间再聚类，可避免按语言分裂话题。

## 5. 多语言处理

| 仓库 | Stars | 语言 | 许可证 | 说明 | 维护状态 |
|---|---|---|---|---|---|
| [facebookresearch/fastText](https://github.com/facebookresearch/fastText) | ~26.6k | C++/Python | MIT | lid.176 模型支持 176 种语言识别，毫秒级；官方仓库已归档但模型稳定 | ⚠️ 已归档，社区 fork（如 [zafercavdar/fasttext-langdetect](https://github.com/zafercavdar/fasttext-langdetect)）可用 |
| [argosopentech/argos-translate](https://github.com/argosopentech/argos-translate) | ~6.3k | Python | MIT | 完全离线的开源神经机器翻译（基于 OpenNMT），适合数据合规要求 | ✅ 活跃 |
| [LibreTranslate/LibreTranslate](https://github.com/LibreTranslate/LibreTranslate) | ~15.5k | Python | AGPL-3.0 | 自托管翻译 API 服务（底层 argos），REST 接口即插即用 | ✅ 极活跃 |
| [ssut/py-googletrans](https://github.com/ssut/py-googletrans) | ~4.3k | Python | MIT | 免费蹭 Google 翻译的非官方封装 | ⚠️ 易被封锁，生产不可靠 |
| [nidhaloff/deep-translator](https://github.com/nidhaloff/deep-translator) | ~2.0k | Python | Apache-2.0 | 聚合 Google/DeepL/微软等多家翻译后端的统一封装 | 低频维护（2024-07） |

注意：本场景"跨语言聚类"优先用跨语言 embedding（不用翻译全文）；翻译只用于前端展示和话题标签。

## 6. 舆情 / 媒体监控完整开源项目

| 项目 | Stars | 语言 | 许可证 | 说明 | 维护状态 |
|---|---|---|---|---|---|
| [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar)（中文舆情热点监控） | ~60.9k | Python | GPL-3.0 | 聚合多个中文热榜+新闻源，做热点去重聚类、关键词订阅与推送，前端开箱即用 | ✅ 极活跃 |
| [666ghj/BettaFish](https://github.com/666ghj/BettaFish)（微舆） | ~41.8k | Python | GPL-2.0 | 多 Agent 舆情分析系统，国内社媒+新闻采集、情感分析、报告生成 | ✅ 极活跃 |
| 思通舆情（Gitee: [stonedtx/yuqing](https://gitee.com/aloofstar/yuqing)） | — | Java | — | 完整商用级开源舆情系统（SpringBoot + Kafka + RabbitMQ + Spider-flow/WebMagic 采集 + Vue 前端） | 维护中，架构参考价值高 |
| [koala73/worldmonitor](https://github.com/koala73/worldmonitor) | ~72.7k | TypeScript | — | 实时全球情报仪表盘：新闻聚合 + 地缘事件 + 市场数据可视化，前端可视化参考价值大 | ✅ 活跃 |
| [hipcityreg/situation-monitor](https://github.com/hipcityreg/situation-monitor) | ~4.2k | TypeScript | — | 全球新闻/市场/地缘态势监控仪表盘 | ✅ 活跃 |
| MediaCloud 组件：[backend](https://github.com/mediacloud/backend)、[web-search](https://github.com/mediacloud/web-search)、[story-indexer](https://github.com/mediacloud/story-indexer) | 小（<300） | Python/JS | AGPL/Apache | 学术界媒体关注度研究（Media Cloud / Media Meter 所属生态）的抓取、索引、检索组件 | 规模小、活跃度一般；主要作为方法论（media attention 度量）参考 |

提示：TrendRadar 与 BettaFish 均面向中文热点，但采集管道、聚类、推送的架构可直接借鉴；worldmonitor 类项目则演示了"全球议程仪表盘"的前端形态。

## 7. 前端可视化仪表盘

| 方案 | Stars | 语言 | 许可证 | 说明 | 维护状态 |
|---|---|---|---|---|---|
| [grafana/grafana](https://github.com/grafana/grafana) | ~75.8k | TypeScript/Go | AGPL-3.0 | 时序监控看板之王，告警成熟，但偏技术指标，业务自助分析弱 | ✅ 极活跃 |
| [metabase/metabase](https://github.com/metabase/metabase) | ~48.3k | Clojure | AGPL（商业版另有） | 对非技术用户最友好：无代码查询构建器、自助仪表盘、可嵌入 | ✅ 极活跃 |
| [apache/superset](https://github.com/apache/superset) | ~74.0k | Python | Apache-2.0 | 功能最全的开源 BI（40+ 图表、SQL Lab、企业级权限），学习曲线较陡 | ✅ 极活跃 |
| [apache/echarts](https://github.com/apache/echarts) | ~66.9k | TypeScript | Apache-2.0 | 自研前端时的图表底座，地图/关系图/时间轴最适合"全球议程地图"类展示 | ✅ 极活跃 |

对比参考：[Metabase vs Superset vs Grafana](https://www.modern-datatools.com/compare/metabase-vs-superset-vs-grafana)、[elest.io 自托管 BI 对比](https://blog.elest.io/apache-superset-vs-metabase-vs-redash-which-open-source-bi-tool-to-self-host-in-2026/)。面向非技术用户的"数据看板"：Metabase 上手最快；若要定制"全球话题地图、议程时间线、传播链路"等专属视图，自研 React + ECharts 是必经之路。

## 8. 低延迟实时架构参考

| 组件 | Stars | 语言 | 许可证 | 角色 | 维护状态 |
|---|---|---|---|---|---|
| [apache/kafka](https://github.com/apache/kafka) | ~33.3k | Java | Apache-2.0 | 新闻事件流总线（采集→NLP→入库解耦） | ✅ 极活跃 |
| [redis/redis](https://github.com/redis/redis) | ~75.7k | C | RSALv2/SSPL（开源边界需注意） | Redis Streams 轻量消息队列 + 去重缓存，中小规模可替代 Kafka | ✅ 极活跃 |
| [celery/celery](https://github.com/celery/celery) | ~28.7k | Python | BSD | Python 分布式任务队列，驱动定时抓取/NLP 批处理 | ✅ 极活跃 |
| [scrapy/scrapy](https://github.com/scrapy/scrapy) | ~63.4k | Python | BSD-3 | 爬虫框架本体 | ✅ 极活跃 |
| [rmax/scrapy-redis](https://github.com/rmax/scrapy-redis) | ~5.6k | Python | MIT | 基于 Redis 的 Scrapy 分布式调度/去重，是"Kafka/Redis + 爬虫调度"的最小实践 | ✅ 活跃 |
| [istresearch/scrapy-cluster](https://github.com/istresearch/scrapy-cluster) | ~1.2k | Python | MIT | Kafka + Redis + Scrapy 的分布式爬虫集群参考实现 | ❌ 停更（2023-11），仅作架构参考 |
| [Gerapy/Gerapy](https://github.com/Gerapy/Gerapy) | ~3.5k | Python | MIT | Scrapy 分布式部署管理面板 | ✅ 活跃 |
| [Boris-code/feapder](https://github.com/Boris-code/feapder) | ~3.7k | Python | Apache | 国产分布式爬虫框架，内置任务调度、报警、去重 | ✅ 极活跃 |

建议架构：RSS/爬虫 →（feedparser/trafilatura 抽取）→ Redis Streams（小规模）或 Kafka（大规模）→ NLP worker（语言识别→embedding→BERTopic 增量聚类）→ PostgreSQL/Elasticsearch → 看板。

## 结论：从零搭建最值得直接复用的组件

1. **数据源层**：各国媒体 RSS 清单（`awesome-rss-feeds`）+ `RSSHub` 补源 + `feedparser` 解析 —— 覆盖主流媒体的最低成本方案；再用 `GDELT DOC API`（直接 REST 或 `gdelt-doc-api`）做全球信号补充。
2. **正文抽取**：`trafilatura` 为主（多语言、评测最优、活跃），`readability-lxml` 兜底，JS 重页面再上 Playwright。
3. **多语言管线**：fastText `lid.176` 语言识别 → `sentence-transformers` 跨语言 embedding（`paraphrase-multilingual-mpnet-base-v2`）→ `BERTopic`（含 online/incremental 模式）做话题聚类；展示层翻译用 `argos-translate`/`LibreTranslate` 离线方案。
4. **架构**：中小规模用 `celery` + Redis Streams + `scrapy-redis` 即可，不必一开始上 Kafka。
5. **前端**：快速验证用 `Metabase`（非技术用户友好）；正式产品的"全球议程地图/时间线"自研 React + `ECharts`。
6. **现成参考实现**：中文场景看 `TrendRadar`（热点聚合推送）和 `BettaFish`（多 Agent 分析），全球仪表盘形态看 `worldmonitor`，商用级舆情架构看 Gitee 上的思通舆情。

主要不确定点：fastText 官方仓库已归档、scrapy-cluster/newscatcher/gdelt-py 已停更或删除，使用时需自行评估 fork 或直接封装底层 API。