"""回放指标计算纯函数测试（evaluate_case_outcome / 聚合 / PASS-FAIL 判定，无需 DB）。"""
from datetime import UTC, datetime

import pytest

from app.assessment.replay import (
    CaseOutcome,
    GroundTruth,
    ReplayCase,
    ReplayReport,
    OriginAccuracy,
    MergeAccuracy,
    EventAccuracy,
    CrossLingualAccuracy,
    _accumulate,
    _finalize,
    _lcs_len,
    evaluate_case_outcome,
    replay_cases,
)


def _case(**gt_overrides):
    gt = {
        "origin_country": "US",
        "origin_source_name": "Reuters",
        "origin_at": datetime(2021, 3, 24, 10, tzinfo=UTC),
        "follower_sequence": [
            {"country_code": "GB", "first_media": "BBC", "lag_hours": 8},
            {"country_code": "FR", "first_media": "AFP", "lag_hours": 12},
            {"country_code": "JP", "first_media": "NHK", "lag_hours": 20},
        ],
        "should_be_agenda_event": True,
        "expected_article_groups": [["a1", "a2", "a3"]],
        "expected_separate_pairs": [("a1", "d1"), ("a2", "d1")],
        "cross_language_pairs": [("a1", "a3")],
    }
    gt.update(gt_overrides)
    return ReplayCase(
        case_id="demo", label="演示", description="",
        ground_truth=GroundTruth(**gt), articles=[],
    )


def _outcome(**kw):
    defaults = {
        "article_topics": {"a1": "T1", "a2": "T1", "a3": "T1", "d1": "T2"},
        "main_topic_id": "T1",
        "origin_country": "US",
        "origin_source_name": "Reuters",
        "origin_confidence": "high",
        "follower_countries": ["GB", "FR", "JP"],
        "event_topic_ids": {"T1"},
    }
    defaults.update(kw)
    return CaseOutcome(**defaults)


def test_perfect_case():
    m = evaluate_case_outcome(_case(), _outcome())
    assert m.origin_country_correct and m.origin_source_correct
    assert m.groups_merged == 1 and m.false_splits == 0 and m.false_merges == 0
    assert m.cross_merged == 1
    assert m.event_detected and m.expected_event
    assert m.follower_matched == 3


def test_origin_mismatch():
    m = evaluate_case_outcome(_case(), _outcome(origin_country="GB", origin_source_name="BBC"))
    assert not m.origin_country_correct and not m.origin_source_correct


def test_false_split_detected():
    """应同组文章散落两个议题 → 误拆 +1，归并不计正确。"""
    m = evaluate_case_outcome(_case(), _outcome(
        article_topics={"a1": "T1", "a2": "T1", "a3": "T9", "d1": "T2"},
    ))
    assert m.groups_merged == 0 and m.false_splits == 1
    # a3 拆出后跨语言对 (a1,a3) 不同议题 → 跨语言未归并
    assert m.cross_merged == 0


def test_unassigned_article_counts_as_split():
    m = evaluate_case_outcome(_case(), _outcome(
        article_topics={"a1": "T1", "a2": "T1", "a3": None, "d1": "T2"},
    ))
    assert m.groups_merged == 0 and m.false_splits == 1


def test_false_merge_detected():
    """干扰文章 d1 与主事件同议题 → 误并 2 对。"""
    m = evaluate_case_outcome(_case(), _outcome(
        article_topics={"a1": "T1", "a2": "T1", "a3": "T1", "d1": "T1"},
    ))
    assert m.false_merges == 2
    assert m.groups_merged == 1  # 主组仍同议题


def test_event_false_positive_and_true_negative():
    neg_case = _case(should_be_agenda_event=False)
    fp = evaluate_case_outcome(neg_case, _outcome())
    assert fp.event_detected and not fp.expected_event  # 误报
    tn = evaluate_case_outcome(neg_case, _outcome(event_topic_ids=set()))
    assert not tn.event_detected and not tn.expected_event  # 真阴性


def test_event_missed():
    m = evaluate_case_outcome(_case(), _outcome(event_topic_ids=set()))
    assert m.expected_event and not m.event_detected  # 漏报


def test_event_on_distractor_topic_also_counts_as_detected():
    """案例任一议题产事件即算检出（负例干扰议题产事件同样是误报）。"""
    neg_case = _case(should_be_agenda_event=False)
    m = evaluate_case_outcome(neg_case, _outcome(event_topic_ids={"T2"}))
    assert m.event_detected


def test_follower_sequence_order_sensitive():
    # 顺序错：LCS 只有 2（GB,FR 相对顺序保持，JP 提前则丢一个）
    m = evaluate_case_outcome(_case(), _outcome(follower_countries=["JP", "GB", "FR"]))
    assert m.follower_matched == 2
    # 缺一国
    m2 = evaluate_case_outcome(_case(), _outcome(follower_countries=["GB", "JP"]))
    assert m2.follower_matched == 2


def test_lcs_basic():
    assert _lcs_len([], ["A"]) == 0
    assert _lcs_len(["A", "B", "C"], ["A", "C"]) == 2
    assert _lcs_len(["A", "B"], ["B", "A"]) == 1


def _report_with(metrics_list):
    report = ReplayReport(
        run_id="t", run_at=datetime.now(UTC), cases_run=len(metrics_list),
        total_articles=0, elapsed_seconds=0.0,
        origin=OriginAccuracy(), merge=MergeAccuracy(),
        event=EventAccuracy(), crosslingual=CrossLingualAccuracy(),
    )
    for m in metrics_list:
        report.case_metrics.append(m)
        _accumulate(report, m)
    _finalize(report)
    return report


def test_aggregation_all_pass():
    m = evaluate_case_outcome(_case(), _outcome())
    # 误报率需要负例样本：补一个真阴性负例
    neg = evaluate_case_outcome(
        _case(should_be_agenda_event=False), _outcome(event_topic_ids=set()),
    )
    report = _report_with([m, neg])
    assert report.origin.origin_country_accuracy == 1.0
    assert report.merge.merge_accuracy == 1.0
    assert report.merge.false_merge_rate == 0.0
    assert report.event.false_positive_rate == 0.0
    assert report.crosslingual.merge_rate == 1.0
    assert report.passed
    assert any("[PASS] 首发国判定准确率" in n for n in report.notes)


def test_aggregation_fail_outputs_deviation_details():
    """不达标指标必须输出 FAIL 与偏差案例明细（开发计划 2.2 不达标处置）。"""
    good = evaluate_case_outcome(_case(), _outcome())
    bad_case = _case()
    bad = evaluate_case_outcome(bad_case, _outcome(origin_country="FR"))
    fp_case = _case(should_be_agenda_event=False)
    fp = evaluate_case_outcome(fp_case, _outcome())
    report = _report_with([good, bad, fp])
    assert not report.passed
    origin_note = next(n for n in report.notes if "首发国判定准确率" in n)
    assert "[FAIL]" in origin_note and "demo" in origin_note  # 偏差案例名单
    fpr_note = next(n for n in report.notes if "事件误报率" in n)
    assert "[FAIL]" in fpr_note  # 1/1 负例误报 = 100% > 20%


def test_empty_cases_raise_runtime_error():
    """空案例集必须显式报错，不允许静默 PASS。"""
    with pytest.raises(RuntimeError, match="回放案例集为空"):
        replay_cases(None, [], embedder=None)


def test_low_confidence_origin_alert_fails_run():
    """low 置信首发仍自动告警 → 整轮 FAIL（详细设计 2.10 红线）。"""
    m = evaluate_case_outcome(_case(), _outcome(
        origin_confidence="low", low_confidence_event_alerts=1,
    ))
    report = _report_with([m])
    assert not report.passed
    assert any("low 置信首发" in n for n in report.notes)


def test_zero_evaluated_cases_never_pass():
    """全部案例回放异常（0 可评估样本）→ 所有指标 FAIL，不允许零样本静默 PASS。"""
    report = _report_with([])
    assert not report.passed
    # 六项指标全部 FAIL（无有效样本）；不得出现任何 [PASS]
    assert not any(n.startswith("[PASS]") for n in report.notes)
    assert sum(1 for n in report.notes if "无有效样本" in n) == 6
