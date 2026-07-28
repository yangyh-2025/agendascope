"""回放案例加载器测试：严格模式（坏文件显式报错）+ 已提交案例集结构校验。"""
import json
import pathlib

import pytest

from app.assessment.replay import load_replay_cases

CASES_DIR = pathlib.Path(__file__).parent / "replay_cases"


def _valid_case(**overrides):
    data = {
        "case_id": "demo-case",
        "label": "演示案例",
        "description": "测试用",
        "ground_truth": {
            "origin_country": "US",
            "origin_source_name": "Reuters",
            "origin_at": "2021-03-24T10:00:00+00:00",
            "follower_sequence": [
                {"country_code": "GB", "first_media": "BBC", "lag_hours": 8},
            ],
            "should_be_agenda_event": True,
            "expected_article_groups": [["a1", "a2"]],
            "expected_separate_pairs": [["a1", "d1"]],
            "cross_language_pairs": [["a1", "a2"]],
        },
        "articles": [
            {"article_id": "a1", "title": "t1", "content": "c1", "url": "https://x/1",
             "country_code": "US", "source_name": "Reuters", "language": "en",
             "published_at": "2021-03-24T10:00:00+00:00",
             "source_media_type": "agency", "time_source": "feed"},
            {"article_id": "a2", "title": "t2", "content": "c2", "url": "https://x/2",
             "country_code": "CN", "source_name": "新华社", "language": "zh",
             "published_at": "2021-03-24T18:00:00+00:00",
             "source_media_type": "agency", "time_source": "feed"},
            {"article_id": "d1", "title": "td", "content": "cd", "url": "https://x/3",
             "country_code": "US", "source_name": "CNN", "language": "en",
             "published_at": "2021-03-24T12:00:00+00:00"},
        ],
    }
    data.update(overrides)
    return data


def _write_case(tmp_path, data, name="replay_case_demo.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_valid_case(tmp_path):
    _write_case(tmp_path, _valid_case())
    cases = load_replay_cases(tmp_path)
    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "demo-case"
    assert len(case.articles) == 3
    assert case.ground_truth.expected_article_groups == [["a1", "a2"]]
    assert case.ground_truth.expected_separate_pairs == [("a1", "d1")]
    assert case.ground_truth.cross_language_pairs == [("a1", "a2")]
    assert case.articles[0].source_media_type == "agency"


def test_missing_dir_returns_empty():
    assert load_replay_cases("/nonexistent/replay/cases") == []


def test_malformed_json_raises(tmp_path):
    (tmp_path / "replay_case_bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        load_replay_cases(tmp_path)


def test_missing_required_field_raises(tmp_path):
    data = _valid_case()
    del data["ground_truth"]["origin_country"]
    _write_case(tmp_path, data)
    with pytest.raises(ValueError, match="origin_country"):
        load_replay_cases(tmp_path)


def test_duplicate_article_id_raises(tmp_path):
    data = _valid_case()
    data["articles"][1]["article_id"] = "a1"
    _write_case(tmp_path, data)
    with pytest.raises(ValueError, match="重复"):
        load_replay_cases(tmp_path)


def test_dangling_group_reference_raises(tmp_path):
    data = _valid_case()
    data["ground_truth"]["expected_article_groups"] = [["a1", "zz"]]
    _write_case(tmp_path, data)
    with pytest.raises(ValueError, match="不存在的 article_id"):
        load_replay_cases(tmp_path)


def test_empty_articles_raises(tmp_path):
    data = _valid_case(articles=[])
    _write_case(tmp_path, data)
    with pytest.raises(ValueError, match="articles 为空"):
        load_replay_cases(tmp_path)


# ---- 已提交案例集（T5.1 交付物）的持续校验 ----

def _committed_cases():
    if not CASES_DIR.exists():
        return []
    return load_replay_cases(CASES_DIR)


def test_committed_case_set_meets_requirements():
    """案例集验收：≥20 案例、≥4 负例、≥3 个含跨语言报道对、时间线自洽。"""
    cases = _committed_cases()
    assert len(cases) >= 20, f"案例数 {len(cases)} < 20（T5.1 要求 ≥20）"
    negatives = [c for c in cases if not c.ground_truth.should_be_agenda_event]
    assert len(negatives) >= 4, f"负例数 {len(negatives)} < 4（事件误报率测量需要）"
    cross = [c for c in cases if c.ground_truth.cross_language_pairs]
    assert len(cross) >= 3, f"含跨语言报道对的案例 {len(cross)} < 3"
    # 必须包含"新疆棉/BCI"类事件
    assert any("xinjiang" in c.case_id or "新疆棉" in c.label for c in cases), "缺少新疆棉/BCI 类案例"


def test_committed_cases_chronology_consistent():
    """每个案例：origin_at == 最早文章时间；跟随国首篇 lag 与标注一致（±1h）。"""
    cases = _committed_cases()
    assert cases, "案例集为空"
    for case in cases:
        gt = case.ground_truth
        articles = sorted(case.articles, key=lambda a: a.published_at)
        earliest = articles[0]
        assert earliest.published_at == gt.origin_at, (
            f"{case.case_id}: origin_at {gt.origin_at} != 最早文章时间 {earliest.published_at}"
        )
        assert earliest.country_code == gt.origin_country, (
            f"{case.case_id}: 最早文章国家 {earliest.country_code} != 标注首发国 {gt.origin_country}"
        )
        # 正例：跟随国 ≥3 且 lag 非负、不超 14 天窗口
        if gt.should_be_agenda_event:
            assert len(gt.follower_sequence) >= 3, f"{case.case_id}: 正例跟随国 <3"
        for f in gt.follower_sequence:
            assert 0 <= f["lag_hours"] <= 14 * 24, f"{case.case_id}: 跟随国 {f} lag 越界"
            # 该国最早文章与标注 lag 一致（±1h）
            country_arts = [a for a in articles if a.country_code == f["country_code"]]
            assert country_arts, f"{case.case_id}: 跟随国 {f['country_code']} 无文章"
            actual_lag = (
                country_arts[0].published_at - gt.origin_at
            ).total_seconds() / 3600.0
            assert abs(actual_lag - f["lag_hours"]) <= 1.0, (
                f"{case.case_id}: 跟随国 {f['country_code']} lag 标注 {f['lag_hours']} "
                f"与实际 {actual_lag:.1f} 偏差 >1h"
            )
        # 跨语言对两端语言必须不同
        lang = {a.article_id: a.language for a in case.articles}
        for a, b in gt.cross_language_pairs:
            assert lang[a] != lang[b], f"{case.case_id}: 跨语言对 ({a},{b}) 语言相同"


def test_committed_cases_groups_and_distractors():
    """干扰文章不得进入主归并组；每个案例 ≥2 个 expected_separate_pairs。"""
    for case in _committed_cases():
        grouped = {aid for g in case.ground_truth.expected_article_groups for aid in g}
        for a, b in case.ground_truth.expected_separate_pairs:
            assert not ({a, b} <= grouped and any(
                a in g and b in g for g in case.ground_truth.expected_article_groups
            )), f"{case.case_id}: separate 对 ({a},{b}) 同处一个归并组"
        assert len(case.ground_truth.expected_separate_pairs) >= 2, (
            f"{case.case_id}: expected_separate_pairs <2（误并率统计样本不足）"
        )
