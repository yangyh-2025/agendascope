"""alerting.engine 纯函数单元测试：条件评估 + condition_extra 契约解析（T4.14）。"""
from types import SimpleNamespace

from app.alerting.engine import (
    combine_and,
    eval_growth_rate,
    eval_neg_ratio,
    eval_top_n,
    parse_conditions,
)


def _rule(**overrides):
    defaults = {
        "id": "00000000-0000-0000-0000-000000000001",
        "condition_type": "growth_rate",
        "condition_value": 100.0,
        "condition_extra": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestParseConditions:
    """condition_extra 契约：API 写入 {"and":[...]}，引擎须兼容 dict 包一层与裸 list。"""

    def test_no_extra(self):
        conds = parse_conditions(_rule())
        assert conds == [("growth_rate", 100.0)]

    def test_dict_wrapped_and(self):
        """回归：API 写入 {"and":[...]} 时叠加条件必须生效（此前按 list 解析导致永不触发）。"""
        rule = _rule(condition_extra={"and": [{"type": "top_n", "value": 10}]})
        conds = parse_conditions(rule)
        assert conds == [("growth_rate", 100.0), ("top_n", 10.0)]

    def test_dict_wrapped_multiple(self):
        rule = _rule(condition_extra={"and": [
            {"type": "top_n", "value": 5},
            {"type": "neg_ratio", "value": 0.5},
        ]})
        conds = parse_conditions(rule)
        assert conds == [
            ("growth_rate", 100.0), ("top_n", 5.0), ("neg_ratio", 0.5),
        ]

    def test_bare_list(self):
        rule = _rule(condition_extra=[{"type": "top_n", "value": 3}])
        conds = parse_conditions(rule)
        assert conds == [("growth_rate", 100.0), ("top_n", 3.0)]

    def test_single_dict_condition(self):
        rule = _rule(condition_extra={"type": "neg_ratio", "value": 0.3})
        conds = parse_conditions(rule)
        assert conds == [("growth_rate", 100.0), ("neg_ratio", 0.3)]

    def test_invalid_items_ignored(self):
        rule = _rule(condition_extra={"and": [
            {"type": "unknown_metric", "value": 1},
            {"type": "top_n"},          # 缺 value
            "not-a-dict",
            {"type": "top_n", "value": 7},
        ]})
        conds = parse_conditions(rule)
        assert conds == [("growth_rate", 100.0), ("top_n", 7.0)]

    def test_unexpected_type_falls_back_to_main(self):
        rule = _rule(condition_extra="bogus-string")
        conds = parse_conditions(rule)
        assert conds == [("growth_rate", 100.0)]

    def test_empty_extra(self):
        assert parse_conditions(_rule(condition_extra={})) == [("growth_rate", 100.0)]
        assert parse_conditions(_rule(condition_extra=[])) == [("growth_rate", 100.0)]
        assert parse_conditions(_rule(condition_extra={"and": []})) == [("growth_rate", 100.0)]


class TestEvalGrowthRate:
    def test_growth_above_threshold(self):
        r = eval_growth_rate(current_count=30, baseline_count=10, threshold_pct=100.0)
        assert r.satisfied and r.value == 200.0

    def test_growth_below_threshold(self):
        r = eval_growth_rate(current_count=15, baseline_count=10, threshold_pct=100.0)
        assert not r.satisfied and r.value == 50.0

    def test_from_zero_baseline(self):
        r = eval_growth_rate(current_count=5, baseline_count=0, threshold_pct=100.0)
        assert r.satisfied and r.value == 100.0

    def test_both_zero_not_satisfied(self):
        assert not eval_growth_rate(0, 0, 50.0).satisfied


class TestEvalTopN:
    def test_rank_within(self):
        assert eval_top_n(3, 5).satisfied

    def test_rank_outside(self):
        assert not eval_top_n(6, 5).satisfied

    def test_rank_none_not_satisfied(self):
        assert not eval_top_n(None, 5).satisfied


class TestEvalNegRatio:
    def test_above_threshold(self):
        assert eval_neg_ratio(0.6, 0.5).satisfied

    def test_equal_not_satisfied(self):
        assert not eval_neg_ratio(0.5, 0.5).satisfied

    def test_none_not_satisfied(self):
        assert not eval_neg_ratio(None, 0.5).satisfied


class TestCombineAnd:
    def test_all_satisfied(self):
        results = [
            eval_growth_rate(30, 10, 100.0),
            eval_top_n(3, 5),
            eval_neg_ratio(0.7, 0.5),
        ]
        assert combine_and(results)

    def test_one_fails(self):
        results = [
            eval_growth_rate(30, 10, 100.0),
            eval_top_n(9, 5),
        ]
        assert not combine_and(results)
