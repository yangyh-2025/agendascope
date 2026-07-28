"""真实管线回放集成测试（需本地 postgres+pgvector，docker compose up -d db）。

注入确定性伪向量（同组文章向量近乎重合、跨组近似正交），驱动真实代码路径：
OnlineAssigner 双阈值归簇 → nextday_merge 次日归并 → detect_media_origin 首发源判定
→ evaluate_conditions/upsert_event 事件判定，并校验指标聚合。
基础设施不可达时由 migrated_db 夹具自动 skip。
"""
import hashlib
import random
from datetime import UTC, datetime, timedelta

import pytest

from app.assessment.replay import (
    GroundTruth,
    ReplayArticle,
    ReplayCase,
    evaluate_case_outcome,
    replay_cases,
)

pytestmark = pytest.mark.integration

_DIM = 768


class DeterministicEmbedder:
    """确定性伪向量：标题前缀 "::" 前为组键；同键向量 ≈ 重合（cos≈1），跨键 ≈ 正交。

    仅用于测试管线机制（归簇/归并/判定路径），不替代真实语义向量模型。
    """

    def __init__(self):
        self._bases: dict[str, list[float]] = {}

    def _base(self, key: str) -> list[float]:
        if key not in self._bases:
            rng = random.Random(int(hashlib.sha256(key.encode()).hexdigest(), 16))
            vec = [rng.gauss(0, 1) for _ in range(_DIM)]
            norm = sum(v * v for v in vec) ** 0.5
            self._bases[key] = [v / norm for v in vec]
        return self._bases[key]

    def embed_article(self, title: str, summary: str | None, content: str | None) -> list[float]:
        key = title.split("::", 1)[0]
        rng = random.Random(int(hashlib.sha256(title.encode()).hexdigest(), 16))
        base = self._base(key)
        noisy = [b + rng.gauss(0, 0.005) for b in base]
        norm = sum(v * v for v in noisy) ** 0.5
        return [v / norm for v in noisy]


def _art(aid, key, country, source, hours, media_type="online", lang="en", time_source="feed"):
    t0 = datetime(2021, 3, 24, 10, tzinfo=UTC)
    return ReplayArticle(
        article_id=aid,
        title=f"{key}::{aid} report on the developing story",
        content=f"{key} {aid} detailed coverage body text.",
        url=f"https://replay.test/{key}/{aid}",
        country_code=country,
        source_name=source,
        language=lang,
        published_at=t0 + timedelta(hours=hours),
        source_media_type=media_type,
        time_source=time_source,
    )


def _positive_case() -> ReplayCase:
    """正例：Reuters 首发（通讯社），GB/FR/JP 三国跟随，含一对中英跨语言报道，1 篇干扰。"""
    articles = [
        _art("a1", "EVT1", "US", "Reuters", 0, media_type="agency"),
        _art("a2", "EVT1", "GB", "BBC", 6, media_type="broadcast"),
        _art("a3", "EVT1", "FR", "AFP", 12, media_type="agency"),
        _art("a4", "EVT1", "JP", "NHK", 20, media_type="broadcast", lang="ja"),
        _art("a5", "EVT1", "CN", "新华社", 26, media_type="agency", lang="zh"),
        _art("d1", "DIST1", "US", "CNN", 3),
    ]
    return ReplayCase(
        case_id="mini-positive",
        label="迷你正例",
        description="管线集成测试夹具",
        ground_truth=GroundTruth(
            origin_country="US",
            origin_source_name="Reuters",
            origin_at=articles[0].published_at,
            follower_sequence=[
                {"country_code": "GB", "first_media": "BBC", "lag_hours": 6},
                {"country_code": "FR", "first_media": "AFP", "lag_hours": 12},
                {"country_code": "JP", "first_media": "NHK", "lag_hours": 20},
            ],
            should_be_agenda_event=True,
            expected_article_groups=[["a1", "a2", "a3", "a4", "a5"]],
            expected_separate_pairs=[("a1", "d1"), ("a3", "d1")],
            cross_language_pairs=[("a1", "a5")],
        ),
        articles=articles,
    )


def _negative_case() -> ReplayCase:
    """负例：仅 1 国 2 篇，无跨国跟随 → 不应产出议程设置事件。"""
    articles = [
        _art("a1", "EVT2", "US", "CNN", 0),
        _art("a2", "EVT2", "US", "Fox News", 5),
    ]
    return ReplayCase(
        case_id="mini-negative",
        label="迷你负例",
        description="管线集成测试夹具",
        ground_truth=GroundTruth(
            origin_country="US",
            origin_source_name="CNN",
            origin_at=articles[0].published_at,
            follower_sequence=[],
            should_be_agenda_event=False,
            expected_article_groups=[["a1", "a2"]],
            expected_separate_pairs=[("a1", "a2")],  # 占位：本案例不评估误并
            cross_language_pairs=[],
        ),
        articles=articles,
    )


def test_pipeline_replay_end_to_end(db):
    """真实管线回放：首发源/归并/事件判定全部命中预期。"""
    cases = [_positive_case(), _negative_case()]
    report = replay_cases(db, cases, embedder=DeterministicEmbedder())

    assert not [n for n in report.notes if n.startswith("[ERROR]")], report.notes
    assert len(report.case_metrics) == 2

    pos = next(m for m in report.case_metrics if m.case_id == "mini-positive")
    assert pos.origin_country_correct, "首发国应判 US（Reuters 最早原创）"
    assert pos.origin_source_correct
    assert pos.origin_confidence == "high", "通讯社原创首发应为 high 置信"
    assert pos.groups_merged == 1 and pos.false_splits == 0, "同事件文章应归入同一议题"
    assert pos.false_merges == 0, "干扰文章不得并入主事件议题"
    assert pos.cross_merged == 1, "中英跨语言报道对应归并"
    assert pos.event_detected, "≥3 国跟随且首发明确应产出 suspected 事件"
    assert pos.follower_matched == 3, "GB/FR/JP 跟随国序列应完整检出且顺序正确"

    neg = next(m for m in report.case_metrics if m.case_id == "mini-negative")
    assert not neg.event_detected, "无跨国跟随不得产出事件（误报红线）"

    # 聚合指标
    assert report.origin.origin_country_accuracy == 1.0
    assert report.merge.merge_accuracy == 1.0
    assert report.event.false_positive_rate == 0.0
    assert report.crosslingual.merge_rate == 1.0


def test_pipeline_low_confidence_origin_no_event(db):
    """首发 time_source='crawled' → low 置信 → 不自动产事件（详细设计 2.10）。"""
    case = _positive_case()
    case.case_id = "mini-crawled-origin"
    case.articles[0].time_source = "crawled"
    case.ground_truth.should_be_agenda_event = False  # low 置信首发不应成立
    report = replay_cases(db, [case], embedder=DeterministicEmbedder())
    m = report.case_metrics[0]
    assert m.origin_confidence == "low"
    assert not m.event_detected, "low 置信首发绝不自动告警"
    assert report.origin.false_origin_alerts == 0
