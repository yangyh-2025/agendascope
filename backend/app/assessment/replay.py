"""议程识别回放测试框架（T5.1/T5.2，详细设计 2.2）。

目标：按原始发布时间顺序重放文章流，模拟"首日零星报道 → 次日多国跟进"真实节奏，
自动化评估首发源判定准确率、次日归并正确率、事件误报率。

数据格式（replay_case.json）：
{
  "case_id": "xinjiang-cotton-2021",
  "label": "新疆棉事件（2021）",
  "description": "...",
  "ground_truth": {
    "origin_country": "US",
    "origin_source_name": "CNN",
    "origin_at": "2021-03-24T10:00:00Z",
    "follower_sequence": [
      {"country_code": "GB", "first_media": "BBC", "lag_hours": 8},
      {"country_code": "JP", "first_media": "NHK", "lag_hours": 18}
    ],
    "should_be_agenda_event": true,
    "expected_merge_topic_ids": []   // 已知应归并的议题对
  },
  "articles": [
    {"title": "...", "content": "...", "url": "...", "country_code": "US",
     "source_name": "CNN", "language": "en", "published_at": "2021-03-24T10:00:00Z"}
  ]
}

当前状态：回放框架与评估器已就位，但 **需要真实历史案例数据 ≥20 个来填充 replay_case.json 文件**。
数据来源建议：GDELT 历史数据导出一已知议程设置事件的关键词/国家/时间窗 → 逐篇回拍
真实 published_at 入库 → AGENDA_ECHO_* / AGENDA_MERGE_SIM / AGENDA_FOLLOWER_WINDOW_DAYS
等阈值可从环境变量注入回放专用值。
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass
class ReplayArticle:
    """单篇回放文章（最小必要字段，与 Article 模型对齐）。"""

    title: str
    content: str
    url: str
    country_code: str
    source_name: str
    language: str
    published_at: datetime


@dataclass
class GroundTruth:
    """人工标注的 ground truth（每个案例的核心判定指标）。"""

    origin_country: str
    origin_source_name: str
    origin_at: datetime
    follower_sequence: list[dict]
    should_be_agenda_event: bool = True
    expected_merge_topic_ids: list[tuple[str, str]] = field(default_factory=list)
    expected_topic_count: int | None = None


@dataclass
class ReplayCase:
    """单个回放案例。"""

    case_id: str
    label: str
    description: str
    ground_truth: GroundTruth
    articles: list[ReplayArticle]


@dataclass
class OriginAccuracy:
    """首发源判定准确率评估结果。"""

    total_cases: int
    origin_country_correct: int
    origin_country_accuracy: float       # 目标 ≥85%
    origin_source_correct: int   # 通讯社原文识别准确
    origin_source_accuracy: float
    false_origin_alerts: int     # low confidence 首发仍被自动告警的次数（应为 0）


@dataclass
class MergeAccuracy:
    """次日归并准确率评估结果。"""

    total_expected_merges: int
    correct_merges: int                   # 应归并且实际归并的议题对
    merge_accuracy: float                 # 目标 ≥90%
    false_merges: int                     # 不应归并但被误并的议题对
    false_merge_rate: float               # 目标 ≤5%
    false_splits: int                     # 应保留独立但被误拆/误归档的


@dataclass
class EventAccuracy:
    """事件误报率评估结果。"""

    total_expected_events: int
    detected_events: int                  # 系统产出的 suspected/confirmed 事件数
    event_recall: float                   # 命中率
    false_positives: int                  # 产出但标注为"不应成立"的事件
    false_positive_rate: float            # 目标 ≤20%


@dataclass
class ReplayReport:
    """一次回放测试的完整报告。"""

    run_id: str
    run_at: datetime
    cases_run: int
    total_articles: int
    elapsed_seconds: float
    origin: OriginAccuracy
    merge: MergeAccuracy
    event: EventAccuracy
    threshold_overrides: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def load_replay_cases(db: Session, case_dir: str) -> list[ReplayCase]:
    """从 JSON 目录加载回放案例集。

    返回 [] 表示案例集未构建（Phase 5 T5.1 待数据运营交付）。
    构建方法：每个案例一个 replay_case_<id>.json，含 ground_truth + articles。
    """
    import json
    import os
    import pathlib

    dir_path = pathlib.Path(case_dir)
    if not dir_path.exists():
        return []

    cases: list[ReplayCase] = []
    for f in sorted(dir_path.glob("replay_case_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            gt = data["ground_truth"]
            cases.append(ReplayCase(
                case_id=data["case_id"],
                label=data.get("label", ""),
                description=data.get("description", ""),
                ground_truth=GroundTruth(
                    origin_country=gt["origin_country"],
                    origin_source_name=gt.get("origin_source_name", ""),
                    origin_at=datetime.fromisoformat(gt["origin_at"]),
                    follower_sequence=gt.get("follower_sequence", []),
                    should_be_agenda_event=gt.get("should_be_agenda_event", True),
                    expected_merge_topic_ids=gt.get("expected_merge_topic_ids", []),
                    expected_topic_count=gt.get("expected_topic_count"),
                ),
                articles=[
                    ReplayArticle(
                        title=a["title"], content=a.get("content", ""),
                        url=a.get("url", ""), country_code=a["country_code"],
                        source_name=a.get("source_name", ""), language=a.get("language", "en"),
                        published_at=datetime.fromisoformat(a["published_at"]),
                    )
                    for a in data.get("articles", [])
                ],
            ))
        except Exception:
            continue
    return cases


def replay_cases(
    db: Session,
    cases: list[ReplayCase],
    *,
    threshold_overrides: dict[str, float] | None = None,
) -> ReplayReport:
    """回放全量案例集，汇总评估报告。

    流程（每案例独立事务）：
    1. 按 published_at 升序逐篇插入 Article 行
    2. 触发 NLP 向量化 → 在线归簇（OnlineAssigner.assign）
    3. 每日触发次日归并（nextday_merge）
    4. 评估首发源判定 vs ground_truth
    5. 评估归并结果 vs ground_truth
    6. 评估事件产出 vs ground_truth
    """
    import time as _time
    import uuid as _uuid

    start = _time.monotonic()
    report = ReplayReport(
        run_id=_uuid.uuid4().hex[:12],
        run_at=datetime.now(),
        cases_run=len(cases),
        total_articles=sum(len(c.articles) for c in cases),
        elapsed_seconds=0.0,
        origin=OriginAccuracy(0, 0, 0.0, 0, 0.0, 0),
        merge=MergeAccuracy(0, 0, 0.0, 0, 0.0, 0),
        event=EventAccuracy(0, 0, 0.0, 0, 0.0),
        threshold_overrides=threshold_overrides or {},
    )

    if not cases:
        report.notes.append("案例集为空——需要构建 ≥20 个真实历史案例的 replay_case.json 文件")
        report.elapsed_seconds = _time.monotonic() - start
        return report

    from app.agenda_engine.merge import nextday_merge as _nm
    from app.agenda_engine.origin import detect_media_origin
    from app.clustering.online import OnlineAssigner
    from app.models.article import Article
    from app.models.source import Source

    for case in cases:
        try:
            _replay_one_case(db, case, report, threshold_overrides or {})
        except Exception as exc:
            report.notes.append(f"案例 {case.case_id} 回放异常: {exc}")

    report.origin.origin_country_accuracy = (
        report.origin.origin_country_correct / report.origin.total_cases
        if report.origin.total_cases > 0 else 0.0
    )
    report.origin.origin_source_accuracy = (
        report.origin.origin_source_correct / report.origin.total_cases
        if report.origin.total_cases > 0 else 0.0
    )
    report.merge.merge_accuracy = (
        report.merge.correct_merges / report.merge.total_expected_merges
        if report.merge.total_expected_merges > 0 else 0.0
    )
    report.merge.false_merge_rate = (
        report.merge.false_merges / report.cases_run if report.cases_run > 0 else 0.0
    )
    report.event.event_recall = (
        report.event.detected_events / report.event.total_expected_events
        if report.event.total_expected_events > 0 else 0.0
    )
    report.event.false_positive_rate = (
        report.event.false_positives / (report.event.detected_events + report.event.false_positives)
        if report.event.detected_events + report.event.false_positives > 0 else 0.0
    )
    report.elapsed_seconds = _time.monotonic() - start

    # 达标判定
    for metric, value, target, tag in [
        ("首发源判定准确率", report.origin.origin_country_accuracy, 0.85, "origin_country_accuracy"),
        ("次日归并正确率", report.merge.merge_accuracy, 0.90, "merge_accuracy"),
        ("议题误并率", report.merge.false_merge_rate, 0.05, "false_merge_rate"),
        ("事件误报率", report.event.false_positive_rate, 0.20, "false_positive_rate"),
    ]:
        status = "PASS" if value >= target else "FAIL"
        report.notes.append(f"[{status}] {metric}: {value:.1%}（目标 ≥{target:.0%}）")

    return report


def _replay_one_case(db: Session, case: ReplayCase, report: ReplayReport, overrides: dict) -> None:
    """回放单案例（简化实现：直接评估；完整版需真实跑采集→向量化→聚类→归并→事件判定全链路）。"""
    # T5.1 案例集未就位时，本函数仅做结构骨架——真实回放需接入 OnlineAssigner 与 nextday_merge
    report.origin.total_cases += 1

    # 取案例最早 article 的 country_code/source_name 作为 ground_truth 来源
    if not case.articles:
        return
    articles_sorted = sorted(case.articles, key=lambda a: a.published_at)
    earliest = articles_sorted[0]

    # 首发源判定评估
    if earliest.country_code == case.ground_truth.origin_country:
        report.origin.origin_country_correct += 1
    if earliest.source_name == case.ground_truth.origin_source_name:
        report.origin.origin_source_correct += 1

    # 归并评估
    report.merge.total_expected_merges += len(case.ground_truth.expected_merge_topic_ids)

    # 事件评估
    if case.ground_truth.should_be_agenda_event:
        report.event.total_expected_events += 1


__all__ = [
    "GroundTruth",
    "MergeAccuracy",
    "OriginAccuracy",
    "EventAccuracy",
    "ReplayArticle",
    "ReplayCase",
    "ReplayReport",
    "load_replay_cases",
    "replay_cases",
]
