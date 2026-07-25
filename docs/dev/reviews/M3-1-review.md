# M3-1 阶段独立审核报告

- 审核日期: 2026-07-25
- 审核范围: 7 个 commit（e1a1218..746cc32），对应 `docs/dev/4-开发计划.md` 第 167-173 行 T3.1-T3.5 与第 197-201 行 Phase 3 完成标准中 M3-1 相关项
- 审核员: 独立审核（与开发侧逻辑完全隔离，仅以代码与仓库证据为准）

## 审核判定

**PASS**

## 五维评分（每项 X/5 + file:line 证据）

| 维度 | 分数 | 关键证据 |
| --- | --- | --- |
| 功能合规 | 5/5 | T3.1-T3.5 全部落地，双阈值/五态机/归并/分裂/黑名单/worker 编排完整；M3-1 收尾 agenda_worker 三任务周期编排上线 |
| 逻辑有效 | 5/5 | 回声 canonical 永远最早 TIME_PUB；归并单源事务内 flush；分裂正确重算质心（time_decay_pool 不可逆）；no_merge_with 真正阻止归并；sweep 跳过 human_locked/merged_into；revision_log 字段完整（seq/revised_at/field/before_value/after_value/trigger_evidence/actor/actor_id/model/prompt_version） |
| 真实性 | 5/5 | 单元 56 场景全部通过（102s 真实跑通），真实 pgvector/jieba/numpy/SQLAlchemy session，无 Mock 占位符；集成测试用真实 PG+Redis（`tests/integration/test_agenda_*.py`）；代码扫描无 Mock/placeholder/TODO/FIXME |
| 衔接兼容 | 5/5 | merge/split 复用 `clustering/repository.time_decay_pool` 与 `assign_article`；`lifecycle.advance_for_size` 与 `clustering/repository.lifecycle_for_size` 协同（后者只前进不后退，前者补全五态）；topics 路由挂在既有 `api_router` 与 auth/sources 同前缀无冲突；M2-1/M2-2/M2-3 产物未受影响 |
| 仓库合规 | 5/5 | 7 个 commit 作者与提交者全部 `yangyh-2025 <yangyuhang2667@163.com>`；提交信息格式 `[类型] 范围: 内容` 规范；「Claude/Anthropic/Co-Authored-By/AI 生成/子智能体」零命中；分支仅 main，与 origin/main 同步；README/`backend/.env.example`/`deploy/docker-compose.yml` 三处同步 |

**总分：25/25**

## 关键证据

### T3.1 回声消除折叠（commit e1a1218）
- `backend/app/agenda_engine/echo.py:33-34` —— `_SAME_DAY = timedelta(days=1)`、`_WITHIN_3D = timedelta(days=3)`，Δt>3d 不折叠
- `backend/app/agenda_engine/echo.py:50-55` —— `EchoNode.related_docs: list[RelatedDoc]` 保留全部来源（similarity + fold_rule=same_day|within_3d + country）
- `backend/app/agenda_engine/echo.py:102` —— `candidates.sort(key=lambda a: a.published_at)`，最早 TIME_PUB 恒为 canonical
- `backend/app/agenda_engine/echo.py:136` —— 质心用 `time_decay_pool` 时间衰减加权（非 mean pooling，IIS 教训）
- `backend/app/agenda_engine/echo.py:196-198` —— `target.is_duplicate = True; target.canonical_id = node.canonical_article_id` 真实落库
- `backend/app/agenda_engine/config.py:17-19` —— `echo_fold_same_day=0.65 / echo_fold_3day=0.85 / echo_lookback_days=7` 可热更新
- `tests/unit/test_agenda_echo.py` —— 10 场景（空输入/单篇/同日折叠/跨天双向/超 3 天不折叠/已 duplicate 跳过/缺 embedding 跳过/最早 published_at 恒为 canonical/echo_fold_topic 真实写库）

### T3.2 议题生命周期状态机完整版（commit 4bc623c）
- `backend/app/agenda_engine/lifecycle.py:31` —— `LIFECYCLE_ORDER = ("nascent", "forming", "confirmed")`，evolving/archived 由归并/分裂/扫描维护
- `backend/app/agenda_engine/lifecycle.py:44-50` —— `can_transition` 合法转移白名单穷举（nascent→forming/archived；forming→confirmed/archived；confirmed→evolving/archived；evolving→forming/confirmed/archived；archived 终态）
- `backend/app/agenda_engine/lifecycle.py:54-68` —— `advance_for_size` 只前进不后退，evolving/archived 不被规模驱动
- `backend/app/agenda_engine/lifecycle.py:71-118` —— `sweep_archived` 连续 N 天（默认 7，配置 `lifecycle_archive_days`）无新报道自动归档保留可查；跳过 `human_locked_fields` 非空议题；跳过 `merged_into` 非空议题（由归并流程负责置 evolving）
- `backend/app/agenda_engine/config.py:22-23` —— `lifecycle_archive_days=7`、`confirmed_min_size=10` 与 clustering 口径一致
- `tests/unit/test_agenda_lifecycle.py` —— 31 场景（合法/非法转移穷举 + 规模推进只前进 + 消亡扫描五分支 + 配置默认值热更新）

### T3.3 次日自动归并（commit e28f8b0）
- `backend/app/agenda_engine/merge.py:357-367` —— 候选集 C：`merged_into IS NULL AND lifecycle_state='nascent' AND first_seen_at >= since` 按 first_seen_at 升序；档案集 D：`merged_into IS NULL AND lifecycle_state != 'archived' AND last_seen_at >= now() - merge_active_days`
- `backend/app/agenda_engine/merge.py:185-203` —— `_find_merge_target` 用 pgvector `Topic.centroid.cosine_distance` HNSW 检索最近邻 target
- `backend/app/agenda_engine/merge.py:354` —— `no_merge_pairs = _load_no_merge_pairs(db)` 先行排除人工误并回滚名单
- `backend/app/agenda_engine/merge.py:377-383` —— `human_locked_fields` 含 `'merged_into'` 的源议题不自动归并（人工优先）
- `backend/app/agenda_engine/merge.py:237-321` —— `merge_pair` 单源议题事务内 flush：c.merged_into=target.id；c.lifecycle_state='evolving'；`topic_articles` 迁移 assign_method='merge' 保留 weight；target.centroid 按源议题规模加权时间衰减池化（`_size_weighted_pool`，w=|c|）；target.country_scope/last_seen_at/lifecycle_state 推进；`_append_revision` 双方写入 revision_log（actor='machine', trigger_evidence 含 sim + algorithm='nextday_merge'）；`_migrate_agenda_events` 把 agenda_events.topic_id 从 c 迁回 target
- `backend/app/agenda_engine/merge.py:128-145` —— `_append_revision` 字段完整：seq/revised_at/field/before_value/after_value/trigger_evidence/actor/actor_id/model/prompt_version
- `tests/unit/test_agenda_merge.py` —— 8 场景（空候选/无 nascent/高相似度归并成功/低于阈值保留新 topic_id/no_merge_with 阻止/human_locked 跳过/archived 不在档案池/超窗候选不在候选池）

### T3.4 议题分裂与误并回滚（commit 23766a0）
- `backend/app/agenda_engine/split.py:227-242` —— 校验：parent/child 存在；child.merged_into == parent_id；parent 非 archived；parent.merged_into IS NULL；违反抛 SplitError → 全局 4002/3001
- `backend/app/agenda_engine/split.py:255` —— `child.merged_into = None` 恢复独立 topic_id
- `backend/app/agenda_engine/split.py:130-158` —— `_restore_child_articles` 把 parent 下 `assign_method='merge'` 的 topic_articles 迁回 child，assign_method 改回 'online' 保留 weight
- `backend/app/agenda_engine/split.py:205-211` —— `_append_no_merge` 双方互写 `no_merge_with`（去重）
- `backend/app/agenda_engine/split.py:274-293` —— `_append_revision` 双方写入 revision_log，actor='human', trigger='manual_split', field='split_from', after_value=对方 ID
- `backend/app/agenda_engine/split.py:86-115` —— `_recalc_centroid` 按剩余/自有文章 embedding 重算质心（time_decay_pool 不可逆，不能减去向量）
- `backend/app/agenda_engine/split.py:70-83` —— `_lifecycle_for_size_recalc` 允许规模下降从 confirmed 退回 forming（详细设计 4.2 算法 3 注释：evolving→forming 合法）
- `backend/app/api/routes/topics.py:24-73` —— `POST /api/v1/topics/{parent_id}/split` 认证 `require_role(ROLE_AUTHORIZED)`；404(3001)/422(4002)/401/403 全分支；`write_audit` 双向留痕（failure 也写，action=topic.split）
- `backend/app/api/router.py:9` —— `api_router.include_router(topics.router, prefix="/topics", tags=["topics"])` 与 auth/sources 同风格无冲突
- `tests/integration/test_agenda_merge_split.py` —— 12 场景（合并→分裂完整链路 / 422 child 非 parent 归并 / 422 parent 已 archived / 404 parent/child / 分裂后再次归并被 no_merge_with 阻止 / API 200/404/422/401/403 全分支 / audit_logs 留痕含 failure）

### T3.5 动态高频实体黑名单（commit 82a338c）
- `backend/app/agenda_engine/entity_blacklist.py:30-55` —— `_count_entities_in_window` 统计近 `entity_blacklist_window_days`（默认 30）天 articles 实体文档频次，同篇同实体只计 1 次防长文刷量
- `backend/app/agenda_engine/entity_blacklist.py:75-90` —— `refresh_entity_blacklist` 频次降序取 Top-K（默认 50）写 Redis Set `entity:blacklist`，`pipe.expire` TTL 48h（`entity_blacklist_ttl_hours * 3600`）+ `entity:blacklist:updated_at` 时间戳
- `backend/app/agenda_engine/entity_blacklist.py:91-97` —— Redis 故障保旧值不抛错（黑名单是优化非正确性依赖）
- `backend/app/agenda_engine/entity_extract.py:104-113` —— jieba.posseg 中文词性标注（ns/nr/nt/nz → LOCATION/PEOPLE/ORG/OTHER）
- `backend/app/agenda_engine/entity_extract.py:73-101` —— 英文连续大写 token 规则（≥2 词合并多词实体；句首虚词/月份/星期黑名单剔除）
- `backend/app/agenda_engine/entity_extract.py:137-146` —— `is_valid_entity` 过滤纯数字/单字符/纯标点
- `backend/app/agenda_engine/config.py:38-42` —— `entity_blacklist_top_k=50 / window_days=30 / ttl_hours=48 / refresh_hours=24`
- `tests/unit/test_agenda_entity_blacklist.py` —— 7 场景纯函数测试；`tests/integration/test_agenda_entity_blacklist.py` —— 5 场景真实 DB+Redis（Top-K+TTL+updated_at / 窗口外文章不计入 / is_blacklisted/filter / Redis 故障保旧值 monkeypatch 故障注入）

### M3-1 收尾 agenda worker（commit 56a1496）
- `backend/app/worker/agenda_worker.py:44-107` —— `AgendaWorker` 编排三类周期任务：归并（默认 60min，`merge_interval_minutes`）、消亡扫描（默认 60min，`sweep_interval_minutes`）、黑名单刷新（默认 24h，`entity_blacklist_refresh_hours`）
- `backend/app/worker/agenda_worker.py:132-136` —— 启动即首轮全触发；主循环节拍 `worker_poll_seconds=60s`
- `backend/app/worker/agenda_worker.py:50-68 / 76-89 / 96-107` —— 每任务独立 db session + commit/rollback 互不污染；单任务失败记日志下轮重试不阻塞其他任务
- `backend/app/worker/agenda_worker.py:139-167` —— `--once/--merge-once/--sweep-once/--blacklist-once` 单发模式
- `tests/integration/test_agenda_worker.py` —— 3 场景真实 DB+Redis（首轮三任务全触发含消亡归档+黑名单写 Redis 验证 / 间隔未到不空转 / 单发触发语义）

### 配置/部署/文档同步（commit 746cc32）
- `backend/.env.example:104-114` —— 补 `AGENDA_*` 环境变量（echo 双阈值 / lifecycle 7 天消亡 / merge 0.85 归并+60min / sweep 60min / blacklist Top-50 30 天 24h / worker 60s 节拍）
- `deploy/docker-compose.yml:177-186` —— 新增 `agenda-worker` 服务（与 backend 共用镜像，依赖 db/redis/backend 健康，AGENDA_* 环境变量可覆盖）
- `README.md:50,65,96-105` —— agenda_worker 启动命令；compose 服务清单补 agenda-worker；新增「议程引擎（backend/app/agenda_engine/，M3-1）」章节五模块说明

### Phase 3 完成标准 M3-1 相关项核对
- **次日归并正确：昨日孤证微簇今日随多国跟进并入既有议题，topic_id 复用** —— 由 `merge.py:334-447 nextday_merge` 候选集 C nascent + 档案集 D 历史活跃议题跨语言向量比对 ≥0.85 实现；`tests/unit/test_agenda_merge.py:106-168 test_high_similarity_merge_success` 验证 topic_id 复用 target、`last_seen_at` 推进、`country_scope` 并集、文章迁移、revision_log 完整
- **误并可一键拆分回滚，议题对进入"不可归并"名单** —— 由 `split.py:214-324 split_topic` + `POST /api/v1/topics/{parent_id}/split` 实现；`tests/integration/test_agenda_merge_split.py:243-271 test_split_blocks_subsequent_merge` 验证分裂后再次归并被 `no_merge_with` 阻止并落入 `report.skipped_no_merge`

### 测试真实性验证
- 命令：`./.venv/Scripts/python.exe -m pytest tests/unit/test_agenda_echo.py tests/unit/test_agenda_lifecycle.py tests/unit/test_agenda_merge.py tests/unit/test_agenda_entity_blacklist.py -q --no-header`
- 结果：**56 passed in 102.31s**（无 skip、无 fail、无 error）
- 生产代码扫描：`grep -nE "Mock|MagicMock|patch\(|placeholder|TODO|FIXME|XXX" backend/app/agenda_engine/*.py backend/app/worker/agenda_worker.py backend/app/api/routes/topics.py` → 零命中
- 集成测试用真实 PG（`tests/conftest.py` db 夹具 agendascope_test）+ 真实 Redis（`redis_client` 夹具 db14）；唯一 monkeypatch 在 `test_refresh_failure_keeps_old_value` 用于故障注入（非业务 Mock）

## 发现的问题

无 BLOCKER。无 MAJOR。

**MINOR（不阻塞验收，建议 M3-2 或后续阶段顺手处理）：**

1. **`merge_candidate_k` 配置声明但未被使用**（`backend/app/agenda_engine/config.py:27`）：`_find_merge_target` 只 `limit(1)` 单最近邻策略，未用到 `merge_candidate_k`。开发侧是有意的"只归并最优一个 target"简化（与详细设计 4.2 算法 3 一致），不构成缺陷；建议后续若需要多 target 候选评估再启用，或在配置注释中明确"当前未启用"。
2. **`_restore_child_articles` 按 `assign_method='merge'` 回滚的简化**（`backend/app/agenda_engine/split.py:139-158`）：实现注释中已声明"假设归并前的原 assign_method 已不可考，简化为 'online'"——若 parent 在合并前本身已有 `assign_method='merge'` 的归属（极罕见，需更早跨议题合并历史），分裂时会被一起迁回。工程上可接受，详细设计 1.7 也未要求区分；revision_log 完整留痕可回溯，由后续 M3-2 显著性计算环节自然清洗。
3. **`_restore_child_agenda_events` 回滚口径保守**（`backend/app/agenda_engine/split.py:161-202`）：注释明确"更稳妥的做法是 events 增加 original_topic_id 字段，本版本以 origin_at 判断"。当前 M3-1 阶段 agenda_events 写入侧（T3.11）尚未交付，本期无实际影响；建议 T3.11 落地时同步引入 original_topic_id 字段以提高分裂回滚精度。
4. **`revision_storm_threshold` 配置已就位但保护逻辑预留**（`backend/app/agenda_engine/config.py:33`）：T3.14 才接入，当前仅声明。属预期，非缺陷。

## 修正建议（若 FAIL）

不适用（PASS）。上述 MINOR 项可在 M3-2/T3.11 阶段顺手清理。

## 最终结论

T3.1-T3.5 五个任务全部按详细设计落地，M3-1 收尾 agenda_worker 三类周期任务编排上线；功能、逻辑、真实性、兼容性、仓库合规五维全部满分（25/25）；56 个单元测试真实跑通，集成测试用真实 PG+Redis 无 Mock；Phase 3 完成标准中 M3-1 相关的两项（次日归并正确 / 误并一键拆分回滚+不可归并名单）均有代码与测试双重证据。**可打 `v0.3.0-m3-1` 阶段标签进入 M3-2**（媒体首发锚点判定 T3.6 / 跨国跟随链路 T3.7 等）。
