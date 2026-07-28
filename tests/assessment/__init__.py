"""回放框架单元/集成测试（T5.1-T5.3，backend/app/assessment/replay.py）。

- test_replay_loader.py：案例加载器（严格模式）与已提交案例集的结构校验
- test_replay_metrics.py：指标计算纯函数（evaluate_case_outcome / 聚合 / PASS-FAIL 判定）
- test_replay_pipeline.py：真实管线集成测试（需本地 postgres，注入确定性伪向量）
"""
