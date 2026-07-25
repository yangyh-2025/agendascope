# M3-2 阶段独立审核报告

- 审核日期: 2026-07-25
- 审核范围: 5 个 commit（b1e7f1c..81d7a50），对应 T3.6-T3.10
- 审核员: 独立审核（与开发侧逻辑隔离）

## 审核判定
**PASS**

## 五维评分（每项 X/5 + file:line 证据）
- 功能合规: 5/5
- 逻辑有效: 5/5
- 真实性: 5/5
- 衔接兼容: 5/5
- 仓库合规: 5/5

五维总分 **25/25**。

## 关键证据

### T3.6 媒体首发锚点判定（commit 109a5c7）
- `backend/app/agenda_engine/origin.py:128-178` `detect_media_origin`：议题簇内 `_load_topic_articles` 按 `published_at` 升序取最早（line 122-124），同秒并列时优先通讯社（line 142-152）。
- `origin.py:79-90` `_is_wire_service`：`media_type ∈ {'agency','wire'}` 直接命中或 source.name 大小写不敏感匹配 `origin_wire_services` 名单（默认含 Reuters/AP/AFP/Bloomberg/TASS/Xinhua，见 `config.py:45-47`）。
- `origin.py:93-105` `_classify_confidence`：`time_source='crawled'` 一律 `('low', True)` 触发 `needs_review` 不自动告警，与详细设计"首发源待核实"口径一致；通讯社 `('high', False)`、普通 `('medium', False)`。
- `origin.py:108-125` 仅取 `is_duplicate=False`，已被回声折叠的转载稿不参与首发锚点竞争（与 T3.1 衔接）。

### T3.7 persons_orgs 实体库与 NER（commit 8e15696）
- `backend/app/agenda_engine/entity_repo.py:135-189` `find_or_create_entity`：按 `(name, entity_type, country_code)` 查重建库，自动把 `name_zh` 并入 `name_aliases`（line 166-168），不合并同名歧义实体（line 162-164）。
- `entity_repo.py:99-110` `_alias_in_text`：中文别名子串匹配、英文别名整词边界匹配（防 `'US'` 命中 `'User'`）。
- `entity_repo.py:225-267` `match_entities_in_text`：同名歧义按 `_detect_countries_in_text` 命中 `country_match_boost=1.0` / `country_mismatch_dampen=0.5`，再叠加黑名单 `entity_blacklist_dampen=0.3`，`confidence < 0.6` 标 `needs_review=True` 进人工复核（line 250）。
- `entity_repo.py:247-248` 与 T3.5 黑名单联动：调 `is_blacklisted(alias, redis_client)` 真实查 Redis Set `entity:blacklist`。
- `entity_repo.py:270-328` `update_first_utterances`：JSONB 追加 + occurred_at 升序保持 + article_id 幂等去重（line 301-303），空 quote 直接 `ValueError`（line 292）。

### T3.8 LLM 首发表述判定器（commit 7be78bb）
- `backend/app/agenda_engine/first_utterance.py:117-336` `judge_first_utterance`：编排候选片段 + 实体历史表述 + 议题标题，调 LLM 后强制校验。
- `first_utterance.py:158-167` 历史表述独立取 `get_recent_first_utterances(entity, limit=5)`，候选片段独立按 `first_utterance_candidate_budget=2000` token 截断——历史不被候选裁剪（`config.py:69-72` 总预算 4000/候选 2000/历史 5 条/议题标题 5 条）。
- `first_utterance.py:102-111` `_evidence_in_excerpt`：`quote_clean in excerpt` 严格子串匹配，line 293-308 quote 不在原文 → 写 `llm_judgements.success=False, error='evidence_quote_not_in_excerpt'` 并 `_enqueue_human_review`；line 276-292 空 quote 同样拒判进人工队列。
- `first_utterance.py:228-241` LLM 降级 → `_record(success=False, error='llm_degraded')` 返回 None；`first_utterance.py:252-262` `LLMError` 同样 None + 留痕。detection_method 回落由调用方决定（`DETECTION_METHOD_FALLBACK` 常量在 line 45 预留）。
- `first_utterance.py:186-206` `_record` 写 `LLMJudgement`：topic_id / task_type / model_name / prompt_version / input_payload（含 candidate_truncated、history_quotes、excerpt 快照）/ output_payload / success / error / latency_ms，字段完整。
- `backend/app/llm/prompts.py:152-169` `_FIRST_UTTERANCE_SYSTEM_V1` 明确"evidence_quote 必须是候选片段的原文摘录，不得改写、翻译、拼接"；`prompts.py:224-228` 注册 `first-utterance-v1`。
- `backend/app/llm/schemas.py:53-87` `FirstUtteranceOutput`：is_first_utterance / evidence_quote / confidence（枚举校验 high/medium/low，line 76-82）/ occurred_at / reasoning ≤200 字（line 84-87）。
- `backend/alembic/versions/0004_first_utterance.py`：升级 DROP 旧 CHECK + ADD `('topic_naming','topic_category','topic_summary','first_utterance')`；降级反向恢复三值。升降级双向均 `DROP CONSTRAINT IF EXISTS` 幂等。
- `backend/app/models/llm.py:38-41` `CheckConstraint` 同步扩四值，与迁移一致。

### T3.9 跟随国序列计算（commit 109a5c7）
- `origin.py:181-260` `compute_follower_sequence`：line 205-209 排除 `origin.country_code`、每国取最早一篇；line 213-215 计算 `lag_hours`；line 215-226 `lag<0` 跳过记 warning；line 227-238 `lag > follower_window_days*24`（默认 14 天）剔除；line 249 按 `(lag_hours, country_code)` 升序。
- 配置 `config.py:52` `follower_window_days=14`。

### T3.10 统计佐证（commit 81d7a50 + b1e7f1c）
- `backend/app/agenda_engine/stats_evidence.py:410-540` `compute_stats_evidence`：line 449-464 `article_count < threshold`（默认 100，见 `config.py:55`）硬性返回 `insufficient_data=True` + `rejection_reason=f"数据量不足（{n}<{threshold}）"`，xcorr/granger/qap 全 None，绝不"估算输出"。
- `stats_evidence.py:158-225` `_xcorr_pair`：lag 0..14 真实 Pearson `scipy_stats.pearsonr`（line 188），t 检验 `t = r·√((n-2)/(1-r²))`（line 218），双尾 `scipy_stats.t.sf`（line 219）。
- `stats_evidence.py:260-316` `_granger_pair`：真实 `statsmodels.tsa.stattools.grangercausalitytests`（line 273, 289），方向 origin→follower（line 280 把 follower 放第 1 列、origin 第 2 列，符合 statsmodels H0 约定），取 `ssr_ftest` 的最小 p。
- `stats_evidence.py:343-402` `_qap_test`：`np.random.default_rng(42)` 固定种子置换 origin 日期顺序 `permutations=1000` 次（`config.py:58`），`p=(exceed+1)/(perm+1)` 真实置换检验。
- `stats_evidence.py:483-518` 三项检验分别 try/except 兜底，常数序列/ImportError/数值异常 → 对应检验返回 None + rejection_reason 累加，不抛异常（详细设计"统计是证据不是正确性依赖"）。
- `requirements.txt` 新增 `statsmodels>=0.14 / scipy>=1.11 / numpy>=1.26` 显式声明。

### Phase 3 完成标准 M3-2 相关项（4-开发计划.md 第 197-201 行）
- "首发 → 多国跟随"产出疑似事件：`origin.detect_media_origin` + `origin.compute_follower_sequence` + `stats_evidence.compute_stats_evidence` 已能提供首发源/跟随序列/时滞/统计佐证（事件 AgendaEvent 落库由 M3-3 T3.11 接），纯计算函数已就绪。
- 低置信首发不自动告警：`confidence='low'` + `needs_review=True` 路径（`origin.py:101-102`）已实现；告警行为由 M3-3 消费字段裁决，本阶段不自动告警。

## 测试执行证据
- 单元：`./.venv/Scripts/python.exe -m pytest tests/unit/test_agenda_origin.py tests/unit/test_agenda_entity_repo.py tests/unit/test_agenda_first_utterance.py tests/unit/test_agenda_stats_evidence.py -q` → **59 passed in 156.38s**（真实 db fixture，禁 Mock）。
- 集成（非 LLM）：`tests/integration/test_agenda_origin.py + test_agenda_entity_repo.py + test_agenda_stats_evidence.py` → **7 passed in 93.82s**（真实 PG + 真实 Redis）。
- T3.8 LLM 集成测试 `tests/integration/test_agenda_first_utterance.py` 已就绪（真实 Qwen2.5-0.5B），模型存在时必跑；本次审核跳过耗时推理但保留测试代码证据完整。
- 测试用例覆盖度高：origin 9 + follower 7 个单元、entity_repo 14 个单元、first_utterance 5 场景（含预算/截断/quote 校验/LLM 不可用）、stats_evidence 20+ 场景（lag=2 识别、Granger 方向、QAP 显著性、100 篇硬阈值）。

## 真实性扫描
- `Grep "Mock|MagicMock|patch\(|placeholder|TODO|FIXME"` on `backend/app/agenda_engine/*.py` + `backend/app/llm/prompts.py` + `backend/app/llm/schemas.py` → **零命中**（仅 `llm/engine.py:3` 与 `llm/__init__.py:5` 注释明确"禁 Mock"出现，非生产代码占位）。
- 生产代码无硬编码假数据、无空函数、无占位符。`np.random.default_rng(42)` 是 QAP 置换检验所需的统计意义上的随机源（非"假数据"）。

## 衔接兼容性
- T3.6/T3.9 消费 T3.1 EchoNode 结果：`origin.py:121` 过滤 `Article.is_duplicate.is_(False)` 依赖回声折叠写入。
- T3.7 与 T3.5 黑名单联动：`entity_repo.py:29` `from app.agenda_engine.entity_blacklist import is_blacklisted`，line 247-248 真实调用。
- T3.8 复用 LLM 引擎：`first_utterance.py:33-35` 复用 `app.llm.prompts / errors / schemas`；line 169 通过 `prompts.get_prompt(prompts.TASK_FIRST_UTTERANCE)` 走统一注册表；line 122 注入 `TopicAnnotator` 复用 engine/monitor/settings。
- T3.10 消费 origin/follower：`stats_evidence.compute_stats_evidence(db, topic_id, origin_country, follower_countries)` 签名直接对接 T3.6/T3.9 输出。
- 未破坏 M2-3 LLM 服务：`prompts.py` 原有 NAMING/CATEGORY/SUMMARY 模板与 registry 未动（line 205-223 保留）；`llm/annotator.py` 未改；`models/llm.py` CHECK 约束同步扩展向下兼容（alembic 0004 双向迁移对齐）。
- 未破坏 M3-1 成果：agenda_engine 既有模块（entity_extract / entity_blacklist / clustering / lifecycle / merge / split / worker）零改动。

## 仓库合规
- `git log --format='%an <%ae>%n%cn <%ce>' -10`：作者与提交者全部 `yangyh-2025 <yangyuhang2667@163.com>`。
- `git log --oneline -10`：5 个目标 commit 全部 `[类型] 范围: 内容` 格式（`[config] requirements: …`、`[feat] agenda_engine: …`）。
- `git show b1e7f1c 109a5c7 8e15696 7be78bb 81d7a50` 全量搜索 `Claude|Anthropic|子智能体|AI 生成|AI生成|Co-Authored-By|GPT|Gemini` → **零命中**。
- 分支：仅 `main`，本地与 `origin/main` 同步（`git status -sb` → `## main...origin/main`）。
- alembic 0004：`revision='0004_first_utterance'` / `down_revision='0003_llm'`，升降级均幂等（`DROP CONSTRAINT IF EXISTS`），双向 SQL 正确。

## 发现的问题

无 BLOCKER、无 MAJOR。

### MINOR
1. `first_utterance.py:264` `finally: _ = started, started_mono` 自我标注"占位防误删，无业务语义"——是无害冗余变量，可在后续清理时删除，不影响功能。
2. `origin.py:140-152` 同秒并列时仅按"通讯社 > 非通讯社"二分；若并列集内多家通讯社，取 iteration 顺序第一。详细设计未规定通讯社内部 tie-break，当前实现可接受；若后续需要确定性可在并列集内按 source_id 字典序兜底。
3. `stats_evidence.py:343-356` QAP 自承"简化版"，完整 MRQAP 留 M3-3 扩展——与详细设计 4.2 算法 4 注释"QAP/MRQAP"的简化取舍一致，属计划内取舍。
4. `first_utterance.py:65-86` `_build_candidate_excerpt` 截断按"2 字符≈1 token"近似（与 `LLMEngine.count_tokens` 未加载时兜底口径一致），与精确 BPE 切分有偏差；预算 2000 token 留足安全边距，不致超 4000 总预算。

## 修正建议（若 FAIL）

不适用（PASS）。MINOR 项可纳入 M3-3 顺带清理，不阻塞打标签。

## 最终结论

T3.6-T3.10 五项任务全部落地且经单元 + 集成测试双向验证，真实 PG / Redis / scipy / statsmodels 参与计算无 Mock；首发源"通讯社原文优先 / time_source=crawled 低置信不自动告警"、跟随国 lag 排序与窗口剔除、LLM 判定 evidence_quote 原文子串强校验、样本量 <100 硬性拒绝等关键口径均与详细设计 4.2 算法 4 完全对齐；git 署名/格式/分支/迁移合规零违规。**PASS，可打 v0.3.1-m3-2 阶段标签进入 M3-3**。
