> **编号**: ADR-012 | **日期**: 2026-07-24 | **作者**: yangyh-2025 | **状态**: 已确认

# ADR-012: 采集调度器——自研 asyncio 调度器替代 celery beat

## 背景

Phase 1 采集调度语义非常简单：按源 `poll_interval_min`（重点源 1–5min、普通源 15min）周期创建 `collection_job` 并触发采集器；`TEMP_FAIL` 任务按 `next_run_at` 退避重试；GDELT 兜底按 15min 独立节奏拉取。ADR-003 已选定 Redis Streams + celery 作为**任务队列**方案（collector→extractor→nlp_pipeline 解耦），开发计划 T1.13 按此写作"celery beat 定时调度"。进入实现时需在"celery beat"与"自研 asyncio 调度器"之间做一次落地选择。

需要强调的是：本 ADR 只替换**定时触发器**（cron 语义），不替换队列（Redis Streams 与 `raw:articles` 入队语义不变，ADR-003 继续有效）。

## 方案对比

| 维度 | celery beat + celery worker | 自研 asyncio 调度器（进程内 asyncio + ThreadPoolExecutor） |
|------|------|------|
| 进程数（单机私有化，少进程易运维） | beat 1 个 + worker 1 个（采集任务再进队列执行） | 1 个（调度与执行同进程，线程池承载阻塞 IO） |
| 调度语义匹配度 | beat 适合固定 crontab；600 源各自独立 interval 需动态读 DB 生成 schedule（需 PeriodicTask 落库或自研 Scheduler 基类） | 天然契合：每 tick 重读 sources 表算出到期源，语义一目了然 |
| 源配置热更新（T1.13：DB 配置 + 重载信号，保存即生效） | 需额外接 django-celery-beat 或自写热载逻辑 | 每 tick 重读 DB 即热更新（≤1 tick 生效），另以 Pub/Sub 信号双保险 |
| 失败治理（六态退避重试） | 与治理状态机的 `next_run_at` 语义重复：beat 重试（countdown/retry）与 collection_jobs 重试两套机制易漂移 | 单一裁决点 `should_crawl`，重试只由治理状态机驱动，无第二套状态 |
| 资源开销（8C16G 单机） | 两个常驻 Python 进程 + broker 往返序列化 | 一个进程，线程池 8 路并发足够承载 ~600 源 15min 节奏 |
| 运维心智（客户零编程基础） | celery 概念多（beat/worker/broker/result backend） | 普通 Python 进程，日志即全部 |
| 横向扩展性 | 任务级扩展强（日百万级任务） | 进程级扩展（单机够用；2.2 万篇/天远低于上限） |

## 决策

**选择自研 asyncio 调度器**（`backend/app/collector/scheduler.py`）：asyncio 事件循环按 tick（默认 30s）扫描 sources 表，到期源与到期 `TEMP_FAIL` 任务提交至 ThreadPoolExecutor（默认 8 线程）执行；GDELT 由独立计时器触发且其任务重试不进入源调度路径。celery 不在 Phase 1 引入；Redis Streams（ADR-003）继续承担 collector→中枢→NLP 的队列解耦。

## 理由

1. **调度语义与 celery 模型不匹配**：本平台是"600 个独立节奏的轮询源"，不是"大量一次性任务"。celery beat 的价值在固定 crontab；动态 per-source interval 仍要自研 schedule 生成，celery 只剩壳。
2. **重试语义唯一性**：治理状态机（六态 + retry_count + next_run_at）是唯一重试裁决点。引入 celery retry 会产生两套重试状态，违背详细设计"should_crawl 统一裁决"。
3. **私有化单机少进程**：每少一个常驻进程，安装包、健康巡检、故障排查都简单一分（ADR-001/ADR-010 同原则）。
4. **热更新零成本**：每 tick 重读 DB 即实现"保存即生效"，与 ADR-011"配置搬进数据库"决策自洽。

## 影响

- **正面**：worker 进程从 2 个减到 1 个；热更新无额外组件；六态流转与重试语义单一可测（`tests/integration/test_governance_db.py` 直接覆盖）。
- **代价**：进程内线程池受 GIL 约束——采集是 IO 密集（网络等待为主），8 线程足够；若未来源规模 ×10 或引入 CPU 密集抓取，重评估"多进程调度分片"或回迁 celery worker。
- **重评估触发器**：单 worker 进程采集吞吐持续低于入库红线（P95 可见延迟 >30min 由调度等待引起）；源规模 >2000；需要跨机分布式采集。
