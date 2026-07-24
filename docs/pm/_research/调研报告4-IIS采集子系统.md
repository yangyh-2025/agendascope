# 调研报告4：IIS 采集（爬虫）子系统深度分析
> 来源：https://github.com/SleepySoft/IntelligenceIntegrationSystem (dev 分支) + 子模块 SleepySoft/IntelligenceCrawler
> 调研日期：2026-07-24。主仓库 Apache-2.0；**IntelligenceCrawler 子模块无 LICENSE 文件**（法律灰色地带：复刻设计而非搬运代码）。

## 总体判断
IIS 采集层 = 15 个媒体插件 + 配置驱动爬虫框架（IntelligenceCrawler）+ 插件式调度引擎。对 30 国/600 源平台：**架构与"配置即爬虫"理念价值极高，任务文件覆盖量太小（~2.5%）只能作种子与模式参考**。建议：复刻架构 + 选择性复用，不直接搬任务文件，也不完全自研。

## CrawlTasks/ 清单（17 个任务文件，15 个媒体 12 国，全部 15 分钟周期）
| 媒体 | 国家 | 方式 |
|---|---|---|
| BBC | 英 | 11 个 RSS + Requests 正文 |
| VOA | 美 | 14 个 RSS + Playwright 正文 |
| 塔斯社 | 俄 | Playwright 抓列表页（6 频道 scroll_pages=5）+ Trafilatura（配置驱动 B 代，任务文件仅 17 行壳） |
| 新华网 | 中 | Requests 抓 5 个列表页 + Trafilatura（走 cn_site_proxy 国内直连分流） |
| 中新网 | 中 | 5 RSS + Playwright 正文 |
| NHK | 日 | RSSDiscoverer + Playwright 正文 + post_extra_action 声明式点"確認しました/次へ"同意弹窗 |
| DW | 德 | 7 RSS + Requests |
| France24 / RFI | 法 | RSS + Playwright |
| El País | 西 | 8 RSS + Playwright |
| 阿纳多卢 / NTV | 土 | RSSDiscoverer + Playwright（stealth） |
| 韩联社 | 韩 | 4 RSS + Playwright |
| 半岛 | 卡塔尔 | 1 RSS + Requests |
| CBC | 加 / ABC | 澳 | RSS + Requests/Playwright |
| Investing.com | 美/全球 | 13 财经 RSS + Playwright |

**G20 缺口**：印度、印尼、巴西、墨西哥、阿根廷、沙特、南非、意大利；美国缺 NYT/CNN/WSJ/AP，中国缺人民日报/央视等。15 → 600 缺口 97.5%。

## 两代任务模式
- A 代（RSS 手写流）：每媒体一个 .py，定制点仅 3 处：feed 列表、正文抓取方式、CSS selector。插件契约：模块级 `module_init(service_context)` + `start_task(stop_event)`。
- B 代（配置驱动流）：任务文件 17 行壳，逻辑全在 `crawler_config_*.py`，由 `CommonIntelligenceCrawlFlow` + `CrawlPipeline` 驱动。**新增无 RSS 媒体 = Playground 调参生成 config + 复制壳，零爬虫代码**。600 源规模下 A 代是反模式，B 代才是方向。

## IntelligenceCrawler 子模块（配置即爬虫）
三段式管线 + 工厂：
```
Fetcher(Requests/Playwright) → Discoverer(RSS/Sitemap/ListPage) → Extractor(Trafilatura/Readability/Newspaper3k/GenericCSS/Crawl4AI/PassThrough)
```
`build_pipeline(config)` 按配置字符串名实例化组件。生成的配置示例（塔斯社，44 行 dict）：fetcher 类型/参数、discoverer 类型、extractor 类型、entry_points 栏目 URL、scroll_pages、post_extra_action 等。

**ListPageDiscoverer（无 RSS 站点的杀手锏）**：对列表页所有 `<a>` 生成"结构化路径签名"（向上 5 层、剔除 odd/even/active 噪音类和工具类、数字泛化 item-123→item-N），对签名聚类，链接数 ≥5 的最大簇即文章列表——免手写 selector。

**CrawlerPlayground**：PyQt5 + QWebEngineView 桌面 GUI，左配参数右侧实时预览，调通后 `CrawlerCodeGenerator` 导出可运行 config 文件；配置 ↔ GUI 双向转换。（600 源平台应改为 Web 表单 + 配置存 DB。）

## 治理子系统（值得照抄）
SQLite `spider_governance.db` 记录每 URL 状态：PENDING/RUNNING/SUCCESS/TEMP_FAIL/PERM_FAIL/SKIPPED + retry_count + next_run_at；`should_crawl(url, max_retries=3)` 是去重与重试统一裁决点；Web 监控页（:8002）树形展示各频道轮次进度/成功率。**弱项：只记录展示，无主动告警**（邮件/webhook 需自建）。

## CrawlerServiceEngine
- 插件扫描 CrawlTasks/*.py，入口约定 module_init/start_task；每插件一守护线程；FlowScheduler(max_concurrency=5, startup_stagger=10s) 节奏与错峰。
- 热重载：watchdog + 300ms 防抖 + blake2b 内容哈希去重 + 单 manager 线程串行执行 + 风暴合并。**注意：dev 分支 watchdog 启动代码被注释（397-401 行），机制写好但未启用**。
- /collect 解耦：采集端按 CollectedData（UUID/token/title/content≥10字符/informant/authors/pub_time/collect_time）POST 到 IHub，任何采集方式皆可接入。
- 防重三层：① governor.should_crawl() SQLite 持久去重（TEMP_FAIL ≤3 次）；② CrawlCache 内存缓存提交失败数据下轮重发；③ 任务内 URL 缓存过滤。

## 反爬栈
Playwright stealth（v1/v2 双版本兼容）、随机 UA、networkidle 等待、scroll_pages 滚动加载、post_extra_action 声明式点弹窗、代理分级（global_site_proxy vs cn_site_proxy 国内外分流，HTTP/SOCKS5）、文章间隔 1s、并发 5 + 启动错峰 10s、SystemdWatchdog 进程级自愈。

## 结论：采集层正确形态
1. **600 源 ≠ 600 个爬虫任务**：预计 80% 主流媒体有官方 RSS → 一个通用 RSS 采集器（feedparser + trafilatura + 治理状态机）+ 每源一行配置（YAML/DB）；种子库用 awesome-rss-feeds（按国家 OPML）+ Media Cloud 目录 + IIS 15 媒体任务参数；RSSHub 补长尾。
2. 无 RSS 的少数站点 → 复刻配置驱动管线（Fetcher/Discoverer/Extractor 三段式）+ ListPageDiscoverer 签名聚类 + Playwright 反爬栈。
3. 直接复用：15 媒体源清单/CSS selector/反爬参数（Apache-2.0）；治理状态机设计；/collect 解耦设计。
4. 复刻而非照搬：插件热重载（其 watchdog 未启用且线程模型不适合 600 源）、Playground 理念（改 Web 表单 + 配置存 DB）。
5. 自建：源失效主动告警（IIS 弱项）。
6. 许可证：主仓库 Apache-2.0 可用；IntelligenceCrawler 子模块无 LICENSE → 复刻设计、不搬代码。
