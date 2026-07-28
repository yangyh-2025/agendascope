"""议程识别回放测试框架（T5.1/T5.2/T5.3，详细设计 2.2）。

按原始发布时间顺序重放文章流，真实跑全链路管线：
  文章向量化（app.nlp.embedding.Embedder）
  → 在线归簇（app.clustering.online.OnlineAssigner）
  → 次日归并（app.agenda_engine.merge.nextday_merge，按 UTC 日界触发）
  → 首发源判定（app.agenda_engine.origin.detect_media_origin）
  → 跟随国序列（app.agenda_engine.origin.compute_follower_sequence）
  → 事件判定（app.agenda_engine.event.evaluate_conditions / upsert_event）
再与 ground_truth 比对计算指标：
  首发源判定准确率 ≥85%、次日归并正确率 ≥90%、议题误并/误拆率 ≤5%、
  事件误报率 ≤20%、跨语言同事件归并率 ≥70%（均为估算目标，见开发计划 2.2）。

不达标处置（开发计划 2.2）：指标不达标输出 FAIL 并给出逐案例偏差明细；
案例集为空时显式报错（RuntimeError），不允许空案例集静默 PASS。

数据格式（tests/assessment/replay_cases/replay_case_<case_id>.json）：
{
  "case_id": "xinjiang-cotton-2021",
  "label": "新疆棉/BCI 事件（2021）",
  "description": "...",
  "ground_truth": {
    "origin_country": "US",
    "origin_source_name": "Reuters",
    "origin_at": "2021-03-24T10:00:00+00:00",
    "follower_sequence": [
      {"country_code": "GB", "first_media": "BBC", "lag_hours": 8}
    ],
    "should_be_agenda_event": true,
    "expected_article_groups": [["a1", "a2", "a5"]],
    "expected_separate_pairs": [["a1", "d1"]],
    "cross_language_pairs": [["a1", "a5"]]
  },
  "articles": [
    {"article_id": "a1", "title": "...", "content": "...", "url": "...",
     "country_code": "US", "source_name": "Reuters", "language": "en",
     "published_at": "2021-03-24T10:00:00+00:00",
     "source_media_type": "agency", "time_source": "feed"}
  ]
}

用法（需本地 docker compose 基础设施：postgres+pgvector）：
  cd backend && python -m app.assessment.replay \
      --case-dir ../tests/assessment/replay_cases \
      --report ../docs/dev/reviews/M5-回放测试报告.md
退出码：0=全部指标 PASS；2=存在 FAIL；1=案例集为空/环境错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time as _time
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

# 指标目标（开发计划 2.2，估算目标）
TARGET_ORIGIN_ACCURACY = 0.85       # 首发国判定准确率
TARGET_MERGE_ACCURACY = 0.90        # 次日归并正确率
TARGET_FALSE_MERGE_RATE = 0.05      # 议题误并率（≤）
TARGET_FALSE_SPLIT_RATE = 0.05      # 议题误拆率（≤）
TARGET_EVENT_FALSE_POSITIVE = 0.20  # 事件误报率（≤）
TARGET_CROSSLINGUAL_RATE = 0.70     # 跨语言同事件归并率

_VALID_MEDIA_TYPES = {"newspaper", "agency", "broadcast", "online"}
_VALID_TIME_SOURCES = {"feed", "crawled", "gdelt"}


@dataclass
class ReplayArticle:
    """单篇回放文章（最小必要字段，与 Article 模型对齐）。"""

    article_id: str           # 案例内唯一标识（ground_truth 分组引用用）
    title: str
    content: str
    url: str
    country_code: str
    source_name: str
    language: str
    published_at: datetime
    source_media_type: str = "online"
    time_source: str = "feed"


@dataclass
class GroundTruth:
    """人工标注的 ground truth（每个案例的核心判定指标）。"""

    origin_country: str
    origin_source_name: str
    origin_at: datetime
    follower_sequence: list[dict]
    should_be_agenda_event: bool = True
    # 同组文章回放后必须归入同一议题（次日归并正确率/误拆率口径）
    expected_article_groups: list[list[str]] = field(default_factory=list)
    # 绝不允许同议题的文章对（误并率口径：干扰文章 × 主事件文章）
    expected_separate_pairs: list[tuple[str, str]] = field(default_factory=list)
    # 不同语言、同一事件的报道对（跨语言归并率口径）
    cross_language_pairs: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ReplayCase:
    """单个回放案例。"""

    case_id: str
    label: str
    description: str
    ground_truth: GroundTruth
    articles: list[ReplayArticle]


@dataclass
class CaseOutcome:
    """单案例回放产物（真实管线输出，供纯函数评估）。"""

    article_topics: dict[str, str | None]   # article_id -> topic_id（未归属为 None）
    main_topic_id: str | None               # 首发文章所在议题
    origin_country: str | None
    origin_source_name: str | None
    origin_confidence: str | None
    follower_countries: list[str]           # 检测到的跟随国序列（按 lag 升序）
    event_topic_ids: set[str]               # 产出 suspected 事件的议题集合
    low_confidence_event_alerts: int = 0    # low 置信首发仍产事件的次数（应为 0）


@dataclass
class CaseMetrics:
    """单案例评估结果（evaluate_case_outcome 纯函数输出）。"""

    case_id: str
    origin_country_correct: bool
    origin_source_correct: bool
    origin_confidence: str | None
    expected_groups: int
    groups_merged: int
    false_splits: int
    separate_pairs: int
    false_merges: int
    cross_pairs: int
    cross_merged: int
    expected_event: bool
    event_detected: bool
    follower_expected: int
    follower_matched: int
    low_confidence_event_alerts: int = 0


@dataclass
class OriginAccuracy:
    """首发源判定准确率评估结果。"""

    total_cases: int = 0
    origin_country_correct: int = 0
    origin_country_accuracy: float = 0.0    # 目标 ≥85%
    origin_source_correct: int = 0
    origin_source_accuracy: float = 0.0
    false_origin_alerts: int = 0            # low confidence 首发仍被自动告警的次数（应为 0）
    failed_cases: list[str] = field(default_factory=list)


@dataclass
class MergeAccuracy:
    """次日归并准确率评估结果。"""

    total_expected_merges: int = 0
    correct_merges: int = 0                 # 应归并且实际归并的文章组
    merge_accuracy: float = 0.0             # 目标 ≥90%
    false_merges: int = 0                   # 不应归并但被误并的文章对
    false_merge_rate: float = 0.0           # 目标 ≤5%
    false_splits: int = 0                   # 应同组但被拆到多议题的组数
    false_split_rate: float = 0.0           # 目标 ≤5%
    failed_cases: list[str] = field(default_factory=list)


@dataclass
class EventAccuracy:
    """事件误报率评估结果。"""

    total_expected_events: int = 0
    detected_events: int = 0                # 应成立且实际产出（命中）
    event_recall: float = 0.0
    true_negatives: int = 0                 # 不应成立且未产出
    false_positives: int = 0                # 产出但标注为"不应成立"的事件
    false_positive_rate: float = 0.0        # 目标 ≤20%（FP / 负例总数）
    false_positive_cases: list[str] = field(default_factory=list)
    missed_cases: list[str] = field(default_factory=list)


@dataclass
class CrossLingualAccuracy:
    """跨语言同事件归并率评估结果。"""

    total_pairs: int = 0
    merged_pairs: int = 0
    merge_rate: float = 0.0                 # 目标 ≥70%
    failed_cases: list[str] = field(default_factory=list)


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
    crosslingual: CrossLingualAccuracy
    case_metrics: list[CaseMetrics] = field(default_factory=list)
    threshold_overrides: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    passed: bool = False


def _parse_dt(value: str, *, ctx: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except Exception as exc:
        raise ValueError(f"{ctx}: 非法时间戳 {value!r}: {exc}") from exc


def _load_case_file(path: pathlib.Path) -> ReplayCase:
    """加载单案例 JSON；任何结构错误显式抛出（不静默跳过）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"案例文件 {path.name} 不是合法 JSON: {exc}") from exc

    ctx = f"案例文件 {path.name}"
    for key in ("case_id", "ground_truth", "articles"):
        if key not in data:
            raise ValueError(f"{ctx}: 缺少必填字段 {key!r}")
    gt = data["ground_truth"]
    for key in ("origin_country", "origin_source_name", "origin_at"):
        if key not in gt:
            raise ValueError(f"{ctx}: ground_truth 缺少必填字段 {key!r}")

    articles: list[ReplayArticle] = []
    seen_ids: set[str] = set()
    for i, a in enumerate(data["articles"]):
        for key in ("article_id", "title", "url", "country_code", "source_name", "language", "published_at"):
            if key not in a:
                raise ValueError(f"{ctx}: articles[{i}] 缺少必填字段 {key!r}")
        aid = str(a["article_id"])
        if aid in seen_ids:
            raise ValueError(f"{ctx}: article_id {aid!r} 重复")
        seen_ids.add(aid)
        media_type = a.get("source_media_type", "online")
        if media_type not in _VALID_MEDIA_TYPES:
            raise ValueError(f"{ctx}: articles[{i}] source_media_type {media_type!r} 非法")
        time_source = a.get("time_source", "feed")
        if time_source not in _VALID_TIME_SOURCES:
            raise ValueError(f"{ctx}: articles[{i}] time_source {time_source!r} 非法")
        articles.append(ReplayArticle(
            article_id=aid,
            title=a["title"],
            content=a.get("content", ""),
            url=a["url"],
            country_code=a["country_code"],
            source_name=a["source_name"],
            language=a["language"],
            published_at=_parse_dt(a["published_at"], ctx=ctx),
            source_media_type=media_type,
            time_source=time_source,
        ))
    if not articles:
        raise ValueError(f"{ctx}: articles 为空")

    def _pairs(key: str) -> list[tuple[str, str]]:
        raw = gt.get(key, [])
        pairs = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError(f"{ctx}: ground_truth.{key} 元素必须为二元组: {item!r}")
            for aid in item:
                if str(aid) not in seen_ids:
                    raise ValueError(f"{ctx}: ground_truth.{key} 引用了不存在的 article_id {aid!r}")
            pairs.append((str(item[0]), str(item[1])))
        return pairs

    groups: list[list[str]] = []
    for g in gt.get("expected_article_groups", []):
        if not isinstance(g, list) or not g:
            raise ValueError(f"{ctx}: expected_article_groups 元素必须为非空数组")
        for aid in g:
            if str(aid) not in seen_ids:
                raise ValueError(f"{ctx}: expected_article_groups 引用了不存在的 article_id {aid!r}")
        groups.append([str(a) for a in g])

    return ReplayCase(
        case_id=str(data["case_id"]),
        label=data.get("label", ""),
        description=data.get("description", ""),
        ground_truth=GroundTruth(
            origin_country=gt["origin_country"],
            origin_source_name=gt["origin_source_name"],
            origin_at=_parse_dt(gt["origin_at"], ctx=ctx),
            follower_sequence=gt.get("follower_sequence", []),
            should_be_agenda_event=bool(gt.get("should_be_agenda_event", True)),
            expected_article_groups=groups,
            expected_separate_pairs=_pairs("expected_separate_pairs"),
            cross_language_pairs=_pairs("cross_language_pairs"),
        ),
        articles=articles,
    )


def load_replay_cases(case_dir: str | os.PathLike) -> list[ReplayCase]:
    """从 JSON 目录加载回放案例集（严格模式：坏文件显式报错，不静默跳过）。

    目录不存在或无任何案例文件时返回 []——由 replay_cases 统一显式报错。
    """
    dir_path = pathlib.Path(case_dir)
    if not dir_path.exists():
        return []
    return [_load_case_file(f) for f in sorted(dir_path.glob("replay_case_*.json"))]


def evaluate_case_outcome(case: ReplayCase, outcome: CaseOutcome) -> CaseMetrics:
    """纯函数：回放产物 × ground_truth → 单案例指标（不依赖 DB，可单测）。

    口径（开发计划 2.2）：
    - 首发源判定：detect_media_origin 输出的国家/来源与标注比对
    - 次日归并正确率：expected_article_groups 中全部文章归入同一议题的组占比
    - 误拆：应同组文章散落到 >1 个议题（含未归属）
    - 误并：expected_separate_pairs 两端文章落入同一议题
    - 跨语言归并率：cross_language_pairs 两端同议题占比
    - 事件：案例任一议题产出 suspected 事件 vs should_be_agenda_event
    - 跟随国序列：检测序列与标注序列的最长公共子序列长度（顺序敏感）
    """
    gt = case.ground_truth
    topics = outcome.article_topics

    # 归并组评估
    expected_groups = len(gt.expected_article_groups)
    groups_merged = 0
    false_splits = 0
    for group in gt.expected_article_groups:
        assigned = {topics.get(a) for a in group}
        assigned.discard(None)
        if len(assigned) == 1 and all(topics.get(a) for a in group):
            groups_merged += 1
        else:
            false_splits += 1

    # 误并评估
    false_merges = 0
    for a, b in gt.expected_separate_pairs:
        ta, tb = topics.get(a), topics.get(b)
        if ta is not None and ta == tb:
            false_merges += 1

    # 跨语言归并评估
    cross_merged = 0
    for a, b in gt.cross_language_pairs:
        ta, tb = topics.get(a), topics.get(b)
        if ta is not None and ta == tb:
            cross_merged += 1

    # 事件评估：案例涉及的任一议题产出 suspected 事件即视为"检出"
    case_topics = {t for t in topics.values() if t is not None}
    event_detected = bool(case_topics & outcome.event_topic_ids)

    # 跟随国序列（LCS，顺序敏感）
    expected_seq = [f["country_code"] for f in gt.follower_sequence]
    follower_matched = _lcs_len(expected_seq, outcome.follower_countries)

    return CaseMetrics(
        case_id=case.case_id,
        origin_country_correct=(outcome.origin_country == gt.origin_country),
        origin_source_correct=(outcome.origin_source_name == gt.origin_source_name),
        origin_confidence=outcome.origin_confidence,
        expected_groups=expected_groups,
        groups_merged=groups_merged,
        false_splits=false_splits,
        separate_pairs=len(gt.expected_separate_pairs),
        false_merges=false_merges,
        cross_pairs=len(gt.cross_language_pairs),
        cross_merged=cross_merged,
        expected_event=gt.should_be_agenda_event,
        event_detected=event_detected,
        follower_expected=len(expected_seq),
        follower_matched=follower_matched,
        low_confidence_event_alerts=outcome.low_confidence_event_alerts,
    )


def _lcs_len(a: list[str], b: list[str]) -> int:
    """最长公共子序列长度（跟随国顺序一致性度量）。"""
    if not a or not b:
        return 0
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, x in enumerate(a, 1):
        for j, y in enumerate(b, 1):
            dp[i][j] = dp[i - 1][j - 1] + 1 if x == y else max(dp[i - 1][j], dp[i][j - 1])
    return dp[len(a)][len(b)]


def replay_cases(
    db: Session,
    cases: list[ReplayCase],
    *,
    embedder: Any,
    threshold_overrides: dict[str, float] | None = None,
) -> ReplayReport:
    """回放全量案例集，汇总评估报告。

    embedder：具备 embed_article(title, summary, content) -> list[float] 的对象
    （生产用 app.nlp.embedding.Embedder；测试可注入确定性伪向量实现）。

    案例集为空时显式抛 RuntimeError——不允许空案例集静默 PASS。
    """
    if not cases:
        raise RuntimeError(
            "回放案例集为空：请先在 tests/assessment/replay_cases/ 构建 ≥20 个 "
            "replay_case_<case_id>.json 标注案例（T5.1），不允许空案例集参与验收"
        )

    start = _time.monotonic()
    report = ReplayReport(
        run_id=_uuid.uuid4().hex[:12],
        run_at=datetime.now(UTC),
        cases_run=len(cases),
        total_articles=sum(len(c.articles) for c in cases),
        elapsed_seconds=0.0,
        origin=OriginAccuracy(),
        merge=MergeAccuracy(),
        event=EventAccuracy(),
        crosslingual=CrossLingualAccuracy(),
        threshold_overrides=threshold_overrides or {},
    )

    for case in cases:
        try:
            outcome = _replay_one_case(db, case, embedder, threshold_overrides or {})
        except Exception as exc:
            db.rollback()
            report.notes.append(f"[ERROR] 案例 {case.case_id} 回放异常: {exc}")
            continue
        metrics = evaluate_case_outcome(case, outcome)
        report.case_metrics.append(metrics)
        _accumulate(report, metrics)

    _finalize(report)
    report.elapsed_seconds = _time.monotonic() - start
    return report


def _accumulate(report: ReplayReport, m: CaseMetrics) -> None:
    """把单案例指标累加进报告（含偏差案例清单）。"""
    o, mg, ev, cl = report.origin, report.merge, report.event, report.crosslingual

    o.total_cases += 1
    if m.origin_country_correct:
        o.origin_country_correct += 1
    else:
        o.failed_cases.append(m.case_id)
    if m.origin_source_correct:
        o.origin_source_correct += 1
    o.false_origin_alerts += m.low_confidence_event_alerts

    mg.total_expected_merges += m.expected_groups
    mg.correct_merges += m.groups_merged
    mg.false_merges += m.false_merges
    mg.false_splits += m.false_splits
    if m.false_splits or m.false_merges or m.groups_merged < m.expected_groups:
        mg.failed_cases.append(m.case_id)

    if m.expected_event:
        ev.total_expected_events += 1
        if m.event_detected:
            ev.detected_events += 1
        else:
            ev.missed_cases.append(m.case_id)
    else:
        if m.event_detected:
            ev.false_positives += 1
            ev.false_positive_cases.append(m.case_id)
        else:
            ev.true_negatives += 1

    cl.total_pairs += m.cross_pairs
    cl.merged_pairs += m.cross_merged
    if m.cross_merged < m.cross_pairs:
        cl.failed_cases.append(m.case_id)


def _finalize(report: ReplayReport) -> None:
    """计算比率指标并输出 PASS/FAIL 判定（不达标给出偏差明细，开发计划 2.2 处置条款）。"""
    o, mg, ev, cl = report.origin, report.merge, report.event, report.crosslingual

    o.origin_country_accuracy = o.origin_country_correct / o.total_cases if o.total_cases else 0.0
    o.origin_source_accuracy = o.origin_source_correct / o.total_cases if o.total_cases else 0.0
    mg.merge_accuracy = mg.correct_merges / mg.total_expected_merges if mg.total_expected_merges else 0.0
    total_separate = sum(m.separate_pairs for m in report.case_metrics)
    mg.false_merge_rate = mg.false_merges / total_separate if total_separate else 0.0
    mg.false_split_rate = mg.false_splits / mg.total_expected_merges if mg.total_expected_merges else 0.0
    ev.event_recall = ev.detected_events / ev.total_expected_events if ev.total_expected_events else 0.0
    negative_cases = ev.false_positives + ev.true_negatives
    ev.false_positive_rate = ev.false_positives / negative_cases if negative_cases else 0.0
    cl.merge_rate = cl.merged_pairs / cl.total_pairs if cl.total_pairs else 0.0

    checks = [
        ("首发国判定准确率", o.origin_country_accuracy, TARGET_ORIGIN_ACCURACY, ">=",
         f"偏差案例（首发国误判）: {', '.join(o.failed_cases) or '无'}"),
        ("次日归并正确率", mg.merge_accuracy, TARGET_MERGE_ACCURACY, ">=",
         f"偏差案例（应并未并/误拆/误并）: {', '.join(mg.failed_cases) or '无'}"),
        ("议题误并率", mg.false_merge_rate, TARGET_FALSE_MERGE_RATE, "<=",
         f"误并 {mg.false_merges}/{total_separate} 对"),
        ("议题误拆率", mg.false_split_rate, TARGET_FALSE_SPLIT_RATE, "<=",
         f"误拆 {mg.false_splits}/{mg.total_expected_merges} 组"),
        ("事件误报率", ev.false_positive_rate, TARGET_EVENT_FALSE_POSITIVE, "<=",
         f"误报案例: {', '.join(ev.false_positive_cases) or '无'}；漏报案例: {', '.join(ev.missed_cases) or '无'}"),
        ("跨语言同事件归并率", cl.merge_rate, TARGET_CROSSLINGUAL_RATE, ">=",
         f"未归并对所在案例: {', '.join(cl.failed_cases) or '无'}"),
    ]
    all_passed = True
    for name, value, target, op, detail in checks:
        ok = value >= target if op == ">=" else value <= target
        if not ok:
            all_passed = False
        report.notes.append(
            f"[{'PASS' if ok else 'FAIL'}] {name}: {value:.1%}"
            f"（目标 {op}{target:.0%}）｜{detail}"
        )
    if o.false_origin_alerts:
        all_passed = False
        report.notes.append(
            f"[FAIL] low 置信首发仍自动告警 {o.false_origin_alerts} 次（应为 0，详细设计 2.10）"
        )
    report.passed = all_passed


def _replay_one_case(
    db: Session,
    case: ReplayCase,
    embedder: Any,
    overrides: dict[str, float],
) -> CaseOutcome:
    """回放单案例：真实跑 向量化→在线归簇→次日归并→首发源判定→事件判定 全链路。

    步骤：
    1. 按案例文章来源建 Source 行（通讯社 media_type='agency'，供首发置信度判定）
    2. 按 published_at 升序逐篇向量化并 OnlineAssigner.assign（真实双阈值在线归簇）
    3. 每个 UTC 日界触发 nextday_merge（真实次日归并）
    4. 对案例涉及的每个议题 detect_media_origin + compute_follower_sequence
       + evaluate_conditions/upsert_event（真实首发源判定与事件判定）
    """
    from app.agenda_engine.event import EventDetectionInput, evaluate_conditions, upsert_event
    from app.agenda_engine.merge import nextday_merge
    from app.agenda_engine.origin import compute_follower_sequence, detect_media_origin
    from app.clustering.online import OnlineAssigner
    from app.clustering.repository import get_assignment
    from app.models.article import Article
    from app.models.source import Source

    assigner = OnlineAssigner(
        t_event=overrides.get("t_event"),
        t_dup=overrides.get("t_dup"),
    )

    # 1) 来源建档（同案例内 source_name+country_code 去重）
    sources: dict[tuple[str, str], Source] = {}
    for ca in case.articles:
        key = (ca.source_name, ca.country_code)
        if key in sources:
            continue
        src = Source(
            name=ca.source_name,
            country_code=ca.country_code,
            homepage_url=f"https://replay.example/{hashlib.sha256(ca.source_name.encode()).hexdigest()[:12]}",
            feed_url=None,
            collect_mode="rss",
            adapter_type="rss",
            media_type=ca.source_media_type,
            language=ca.language,
            poll_interval_min=5,
            audience_weight=10.0,
        )
        db.add(src)
        sources[key] = src
    db.flush()

    # 2) 按发布时间升序重放：向量化 → 在线归簇；日界触发次日归并
    articles_sorted = sorted(case.articles, key=lambda a: (a.published_at, a.article_id))
    case_start = articles_sorted[0].published_at
    art_map: dict[str, Article] = {}
    current_day = articles_sorted[0].published_at.date()
    for ca in articles_sorted:
        if ca.published_at.date() != current_day:
            # UTC 日界：触发次日归并（候选窗口覆盖案例全程）
            nextday_merge(db, candidate_since=case_start - timedelta(days=1))
            db.commit()
            current_day = ca.published_at.date()
        embedding = embedder.embed_article(ca.title, None, ca.content)
        article = Article(
            source_id=sources[(ca.source_name, ca.country_code)].id,
            url=ca.url,
            url_hash=hashlib.sha256(ca.url.encode()).hexdigest(),
            title=ca.title,
            content=ca.content,
            summary=ca.content[:200] if ca.content else None,
            language=ca.language,
            published_at=ca.published_at,
            time_source=ca.time_source,
            country_code=ca.country_code,
            embedding=embedding,
            source_channel="rss",
            content_status="full",
            visible_at=ca.published_at,
        )
        db.add(article)
        db.flush()
        assigner.assign(db, article)
        db.commit()
        art_map[ca.article_id] = article
    # 收尾再跑一轮归并（末日微簇）
    nextday_merge(db, candidate_since=case_start - timedelta(days=1))
    db.commit()

    # 3) 汇总文章→议题归属
    article_topics: dict[str, str | None] = {}
    for aid, art in art_map.items():
        assignment = get_assignment(db, art.id)
        article_topics[aid] = str(assignment.topic_id) if assignment else None

    main_article = art_map[articles_sorted[0].article_id]
    main_assignment = get_assignment(db, main_article.id)
    main_topic_id = str(main_assignment.topic_id) if main_assignment else None

    # 4) 案例涉及的每个议题：首发源判定 + 跟随国序列 + 事件判定
    event_topic_ids: set[str] = set()
    low_conf_alerts = 0
    origin_country = origin_source = origin_conf = None
    follower_countries: list[str] = []
    case_topics = sorted({t for t in article_topics.values() if t is not None})
    for tid in case_topics:
        topic_uuid = _uuid.UUID(tid)
        origin = detect_media_origin(db, topic_uuid)
        followers = compute_follower_sequence(db, topic_uuid, origin) if origin else []
        decision = evaluate_conditions(db, EventDetectionInput(
            topic_id=topic_uuid,
            media_origin=origin,
            followers=followers,
            stats=None,
            detection_method="media_time_fallback",
        ))
        if decision.should_create:
            upsert_event(db, EventDetectionInput(
                topic_id=topic_uuid,
                media_origin=origin,
                followers=followers,
                stats=None,
                detection_method="media_time_fallback",
            ), decision)
            event_topic_ids.add(tid)
            if origin is not None and origin.confidence == "low":
                # 低置信首发绝不应自动告警（详细设计 2.10）；发生即计数
                low_conf_alerts += 1
        if tid == main_topic_id and origin is not None:
            origin_country = origin.country_code
            origin_source = origin.source_name
            origin_conf = origin.confidence
            follower_countries = [f.country_code for f in followers]
    db.commit()

    return CaseOutcome(
        article_topics=article_topics,
        main_topic_id=main_topic_id,
        origin_country=origin_country,
        origin_source_name=origin_source,
        origin_confidence=origin_conf,
        follower_countries=follower_countries,
        event_topic_ids=event_topic_ids,
        low_confidence_event_alerts=low_conf_alerts,
    )


def render_markdown(report: ReplayReport) -> str:
    """把回放报告渲染为 markdown（M5 回放测试报告正文）。"""
    o, mg, ev, cl = report.origin, report.merge, report.event, report.crosslingual
    lines = [
        f"# M5 回放测试报告（run_id={report.run_id}）",
        "",
        f"- 运行时间: {report.run_at.isoformat()}",
        f"- 案例数: {report.cases_run}；文章总数: {report.total_articles}；耗时: {report.elapsed_seconds:.1f}s",
        f"- 阈值覆盖: {report.threshold_overrides or '无'}",
        f"- 总体结论: **{'PASS' if report.passed else 'FAIL'}**",
        "",
        "## 指标汇总（目标见开发计划 2.2，均为估算目标）",
        "",
        "| 指标 | 实测 | 目标 | 结论 |",
        "|------|------|------|------|",
        f"| 首发国判定准确率 | {o.origin_country_accuracy:.1%}（{o.origin_country_correct}/{o.total_cases}） | ≥85% | {'PASS' if o.origin_country_accuracy >= TARGET_ORIGIN_ACCURACY else 'FAIL'} |",
        f"| 首发源判定准确率 | {o.origin_source_accuracy:.1%}（{o.origin_source_correct}/{o.total_cases}） | 参考项 | - |",
        f"| 次日归并正确率 | {mg.merge_accuracy:.1%}（{mg.correct_merges}/{mg.total_expected_merges} 组） | ≥90% | {'PASS' if mg.merge_accuracy >= TARGET_MERGE_ACCURACY else 'FAIL'} |",
        f"| 议题误并率 | {mg.false_merge_rate:.1%}（{mg.false_merges} 对） | ≤5% | {'PASS' if mg.false_merge_rate <= TARGET_FALSE_MERGE_RATE else 'FAIL'} |",
        f"| 议题误拆率 | {mg.false_split_rate:.1%}（{mg.false_splits} 组） | ≤5% | {'PASS' if mg.false_split_rate <= TARGET_FALSE_SPLIT_RATE else 'FAIL'} |",
        f"| 事件命中率 | {ev.event_recall:.1%}（{ev.detected_events}/{ev.total_expected_events}） | 参考项 | - |",
        f"| 事件误报率 | {ev.false_positive_rate:.1%}（{ev.false_positives} 例） | ≤20% | {'PASS' if ev.false_positive_rate <= TARGET_EVENT_FALSE_POSITIVE else 'FAIL'} |",
        f"| 跨语言同事件归并率 | {cl.merge_rate:.1%}（{cl.merged_pairs}/{cl.total_pairs} 对） | ≥70% | {'PASS' if cl.merge_rate >= TARGET_CROSSLINGUAL_RATE else 'FAIL'} |",
        f"| low 置信首发自动告警 | {o.false_origin_alerts} 次 | 0 | {'PASS' if o.false_origin_alerts == 0 else 'FAIL'} |",
        "",
        "## 逐案例明细",
        "",
        "| 案例 | 首发国 | 首发源 | 归并组 | 误并 | 误拆 | 跨语言 | 事件(预期/检出) | 跟随国(中/预期) |",
        "|------|--------|--------|--------|------|------|--------|------------------|------------------|",
    ]
    for m in report.case_metrics:
        lines.append(
            f"| {m.case_id} | {'✓' if m.origin_country_correct else '✗'} "
            f"| {'✓' if m.origin_source_correct else '✗'} "
            f"| {m.groups_merged}/{m.expected_groups} | {m.false_merges} | {m.false_splits} "
            f"| {m.cross_merged}/{m.cross_pairs} "
            f"| {'Y' if m.expected_event else 'N'}/{'Y' if m.event_detected else 'N'} "
            f"| {m.follower_matched}/{m.follower_expected} |"
        )
    lines += ["", "## 判定与偏差明细", ""]
    lines += [f"- {n}" for n in report.notes]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：python -m app.assessment.replay --case-dir ... --report ..."""
    parser = argparse.ArgumentParser(description="议程识别回放测试（T5.1-T5.3）")
    parser.add_argument("--case-dir", default="../tests/assessment/replay_cases",
                        help="回放案例目录（默认 ../tests/assessment/replay_cases）")
    parser.add_argument("--report", default="", help="markdown 报告输出路径（默认只打印到 stdout）")
    parser.add_argument("--t-event", type=float, default=None, help="覆盖在线归簇阈值 T_event")
    parser.add_argument("--t-dup", type=float, default=None, help="覆盖判重阈值 T_dup")
    args = parser.parse_args(argv)

    try:
        cases = load_replay_cases(args.case_dir)
    except ValueError as exc:
        print(f"[错误] 案例集加载失败: {exc}", file=sys.stderr)
        return 1
    if not cases:
        print(f"[错误] 回放案例集为空: {args.case_dir}（不允许空案例集静默 PASS）", file=sys.stderr)
        return 1
    print(f"[信息] 加载案例 {len(cases)} 个，文章 {sum(len(c.articles) for c in cases)} 篇")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import get_settings  # 复用主配置 DATABASE_URL

    engine = create_engine(get_settings().database_url)
    session_factory = sessionmaker(bind=engine)

    from app.nlp.embedding import Embedder

    embedder = Embedder()

    overrides: dict[str, float] = {}
    if args.t_event is not None:
        overrides["t_event"] = args.t_event
    if args.t_dup is not None:
        overrides["t_dup"] = args.t_dup

    with session_factory() as db:
        try:
            report = replay_cases(db, cases, embedder=embedder, threshold_overrides=overrides)
        except RuntimeError as exc:
            print(f"[错误] {exc}", file=sys.stderr)
            return 1

    md = render_markdown(report)
    print(md)
    if args.report:
        out = pathlib.Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"[信息] 报告已写入 {out}")
    return 0 if report.passed else 2


__all__ = [
    "CaseMetrics",
    "CaseOutcome",
    "CrossLingualAccuracy",
    "EventAccuracy",
    "GroundTruth",
    "MergeAccuracy",
    "OriginAccuracy",
    "ReplayArticle",
    "ReplayCase",
    "ReplayReport",
    "evaluate_case_outcome",
    "load_replay_cases",
    "main",
    "render_markdown",
    "replay_cases",
]

if __name__ == "__main__":
    sys.exit(main())
