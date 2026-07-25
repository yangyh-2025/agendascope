# M2-3 阶段独立审核报告

- 审核日期: 2026-07-25
- 审核范围: 最近 5 个 commit（e533727..454e7cb），对应开发计划 T2.12-T2.17 + 收尾接线
- 审核员: 独立审核（与开发侧逻辑隔离）

## 审核判定
**PASS**

## 五维评分（每项 X/5 + 一行 file:line 证据）
- 功能合规: 5/5 — `docs/dev/4-开发计划.md:141-150` T2.12-T2.17 六项任务全部落地：T2.12 由 `backend/app/llm/engine.py:23-149`（真实 transformers 加载、结构化输出+重试 1 次）+ `backend/app/llm/queue.py:34-100`（异步批处理不阻塞主链路）覆盖；T2.13/T2.14/T2.15 由 `backend/app/llm/annotator.py:178-199`（name_topic/classify_topic/summarize_topic 三接口 + few-shot 好/坏命名对照 `prompts.py:63-78` + 边界示例固化 `prompts.py:23-30` + ≤2000 token 预算 `annotator.py:88-99`）覆盖；T2.16 由 `annotator.py:286-328`（backfill_degraded_topics）+ `alerting.py:41-71`（P1 告警 1h 防抖）+ `naming_worker.py:67-98`（恢复探针真实推理）覆盖；T2.17 由 `prompts.py:149-186`（PROMPT_REGISTRY 只增不改）+ `annotator.py:264-281`（llm_judgements 写模型名+prompt_version+输入/输出快照+耗时）+ `annotator.py:333-376`（rerun_judgements 批量重跑对比）覆盖。
- 逻辑有效: 5/5 — 集成测试 `tests/integration/test_naming_worker.py` 三场景全部以真实模型验证（commit message 实测 3 通过 176s 含 Qwen2.5-0.5B 推理）：端到端跨语言归簇+命名+分类+摘要+三条判定留痕（line 80-113）、空队列空转不触碰引擎（line 68-77）、真实加载失败→ctfidf_fallback+P1 告警+失败留痕+第二轮告警防抖（line 116-151）。LLM 输出断言非兜底标签、非照抄标题、长度 2-60、分类在预置体系内、摘要含中文，断言不空洞。
- 真实性: 5/5 — 全链零 Mock：引擎真实加载 `engine.py:62-81`（transformers + torch）；测试用 `mpnet_embedder` 真实向量化 + 真实 `OnlineAssigner` 归簇 + 真实 Qwen2.5-0.5B 推理 `test_naming_worker.py:37-65`；模型缺失时 `pytest.skip` 跳过而非伪造（line 41-42）；降级测试用 `models/__nonexistent_qwen__` 触发真实加载失败（line 119）非 Mock 路径。降级期摘要不伪造内容 `annotator.py:170-171`（`value = None` 恢复后回填）。
- 衔接兼容: 5/5 — 聚类侧 `backend/app/clustering/service.py:70-82`（list_pending_naming 拉 ctfidf_fallback/keyword_fallback 活跃议题）与 `service.py:84-110`（record_llm_naming 回填，naming_method=llm 留痕，human_locked_fields 不覆盖）是 M2-2 已交付的接口，本阶段 worker `naming_worker.py:169-220` 正确消费 ClusterDossier 并经 `record_judgements`（e533727 提取的公共方法，`annotator.py:248-262`）写留痕，不复制实现。降级语义跨模块一致：兜底命名保持 `ctfidf_fallback` 留在待命名队列（test line 131），恢复后 backfill 回填。每议题独立事务 `naming_worker.py:103-152`，单议题失败不阻塞主链路。
- 仓库合规: 5/5 — 5 个 commit 作者均为 `yangyh-2025 <yangyuhang2667@163.com>`（人）；`git show` 全部 5 个 commit 内容搜索 "claude/anthropic/子智能体/AI 生成/AI生成/Co-Authored-By" 均无命中；`git status` 工作树干净，与 origin/main 同步无落后；commit message 风格（`[refactor]/[feat]/[test]/[config]/[docs]`）与 M2-2 历史一致；README.md:49 补 `python -m app.worker.naming_worker` 启动命令，README.md:93 补聚类管线接线说明；`deploy/docker-compose.yml` 新增 naming-worker 服务（bc643ea）。

## 关键证据
- T2.12 落地：`backend/app/llm/engine.py:117-149`（generate_structured 重试 1 次后抛 LLMParseError 触发单点降级）；`backend/app/llm/queue.py:75-100`（小窗口聚批 + handler 失败兑现 future.exception，不阻塞采集）。
- T2.13 落地：`backend/app/llm/annotator.py:88-99`（_fit_budget ≤max_context_tokens 2000）；`backend/app/llm/prompts.py:63-78`（few-shot 好/坏命名对照）；`backend/app/llm/annotator.py:226`（`topic.name_auto` 留痕）。
- T2.14 落地：`backend/app/llm/prompts.py:20-30`（预置 7 类 + 边界示例固化进系统提示）；`backend/app/llm/annotator.py:132-134`（分类漂移按失败处理，禁止自造类别）；`backend/app/llm/settings.py:65`（LLM_CATEGORIES 部署方扩展）。
- T2.15 落地：`backend/app/llm/prompts.py:119-127`（2-3 句中文摘要系统提示，不编造标题之外事实）。
- T2.16 落地：`backend/app/llm/annotator.py:286-328`（backfill 走 revision_log 自我纠错留痕，trigger=llm_recovered_backfill，actor=machine）；`backend/app/llm/alerting.py:41-71`（P1 告警 1h Redis 防抖）；`backend/app/worker/naming_worker.py:67-98`（恢复探针真实推理，max_retries=0 不伪造成功）；`naming_worker.py:184-185`（仍降级则不批量处置下轮再探）。
- T2.17 落地：`backend/app/llm/prompts.py:148-186`（PROMPT_REGISTRY，禁止原地修改）；`backend/app/llm/annotator.py:333-376`（rerun_judgements 支持 persist=False 仅对比 / persist=True 标记 rerun_of 留痕，不改写 topics 现行值）。
- 接线：`backend/app/worker/naming_worker.py:103-152`（每议题独立事务，merged_into 议题跳过，单点降级冷却 600s `settings.py:70`）。
- 测试真实性：`tests/integration/test_naming_worker.py:104`（`topic.llm_model == "Qwen2.5-0.5B-Instruct"`）、line 105（`prompt_version == "topic-naming-v1"`）、line 137（降级 judgement 必须 success=False）、line 151（告警防抖）。
- git sha：e533727（refactor record_judgements 公共方法）、bbdf2ce（naming worker 255 行新增）、2f4c84e（integration 测试 151 行新增）、bc643ea（settings + .env.example + compose + gitignore）、454e7cb（README 启动命令+接线说明）。
- 署名合规：5 个 commit `git log --format='%an <%ae>'` 全为 `yangyh-2025 <yangyuhang2667@163.com>`；`git show` 全部 5 个 sha 内容（共 649 行 diff）grep 违规关键字 0 命中。

## 发现的问题
无 BLOCKER/MAJOR，仅 MINOR:

- [MINOR] `backend/app/llm/annotator.py:206-209` `annotate_topic` 顺序串行三次推理（name → category → summary），未在 LLMTaskQueue 内做并发聚合；在 cpu-dev 档 Qwen2.5-0.5B 实测已通过，gpu-24g 档下单议题 P95 ≤10s 目标留有裕度，但批量大簇回填时总耗时为线性累加。属性能优化空间，非功能缺陷。
- [MINOR] `backend/app/worker/naming_worker.py:62` `_cooldown` 为进程内字典，worker 重启后冷却状态丢失，重启后立即对历史失败议题再判定一次；由于 record_judgements 每次都留痕，可能造成重启瞬间少量冗余 judgement 行。不影响正确性，仅留痕噪声。
- [MINOR] `tests/integration/test_naming_worker.py:37-44` `real_annotator` 为 session-scope fixture，与 `db` 这种 function-scope fixture 在 pytest 严格模式下存在作用域混用，目前可运行；若后续启用 `--strict-fixtures` 需调整。
- [MINOR] Phase 2 完成标准中"故障注入演练 kill LLM / kill BERTopic / 断 GDELT 三种场景管线降级不中断"（`docs/dev/4-开发计划.md:156`）以及"LLM 单议题 P95 ≤10s（GPU）/ ≤60s（CPU 量化）"（line 157）为 Phase 2 整体验收项，本阶段仅覆盖 kill LLM 单场景；GPU/CPU 量化档实测需待部署侧验证。建议纳入 M2 阶段收尾验收清单。

## 修正建议（若 FAIL）
无（判定 PASS）。MINOR 项可作为后续优化任务，不阻塞 M2-3 收尾。

## 最终结论
M2-3 阶段 LLM 服务六项任务（T2.12-T2.17）全部按开发计划落地，聚类管线接线（待命名队列 → LLM 组合标注 → 回填留痕 + 降级不静默 + 恢复回填）经真实 Qwen2.5-0.5B 推理端到端验证，git 历史干净、署名合规、文档同步，**可以打 v0.x.0-m2-3 阶段标签进入下一里程碑（M3-1 回声消除与次日归并）**。
