"""议程引擎（agenda_engine）：回声消除、议题生命周期、次日归并、分裂回滚、实体黑名单。

对应开发计划 Phase 3 M3-1：
- echo.py              T3.1 回声消除折叠
- lifecycle.py         T3.2 议题生命周期状态机完整版（nascent/forming/confirmed/evolving/archived）
- merge.py             T3.3 次日自动归并（topic_id 复用 + revision_log 留痕）
- split.py             T3.4 议题合并/分裂与误并回滚（不可归并名单）
- entity_blacklist.py  T3.5 动态高频实体黑名单（Redis Set + 24h 刷新）
- config.py            AgendaSettings（AGENDA_ 前缀环境变量）

M3-2/M3-3 已在包内扩展：origin.py（首发锚点+跟随序列）、entity_repo.py（实体库）、
first_utterance.py（LLM 首发判定）、stats_evidence.py（统计佐证）、event.py（事件判定）、
final_review.py（LLM 终审）、revision.py（增量重估/人工优先）、confidence.py（置信度升降）、
snapshot.py（快照）；detection.py 为上述孤岛函数的编排器（完整检测主链路），
由 app.worker.detection_worker 周期驱动。
"""
