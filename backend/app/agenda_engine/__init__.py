"""议程引擎（agenda_engine）：回声消除、议题生命周期、次日归并、分裂回滚、实体黑名单。

对应开发计划 Phase 3 M3-1：
- echo.py              T3.1 回声消除折叠
- lifecycle.py         T3.2 议题生命周期状态机完整版（nascent/forming/confirmed/evolving/archived）
- merge.py             T3.3 次日自动归并（topic_id 复用 + revision_log 留痕）
- split.py             T3.4 议题合并/分裂与误并回滚（不可归并名单）
- entity_blacklist.py  T3.5 动态高频实体黑名单（Redis Set + 24h 刷新）
- config.py            AgendaSettings（AGENDA_ 前缀环境变量）

后续 Phase 3 M3-2/M3-3 在此包内扩展：实体库/首发源判定/统计佐证/事件判定/终审官/快照。
"""
