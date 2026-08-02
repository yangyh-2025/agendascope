"""T2.8 在线增量双阈值归簇集成测试：真实 mpnet 向量 + 测试库 PG。

阈值判定用实测相似度自适应标定（阈值为配置项，算法与向量均真实）：
- T_event 归簇：改写报道与议题质心实测相似度 s，t_event=s-0.02 / t_dup=s+0.02 → 走归簇路径
- T_dup 判重：近转载报道实测相似度 s，t_dup=s-0.02 → 走判重路径（is_duplicate 标记）
- 默认阈值下跨语言同事件报道（实测 ~0.6-0.75 < 0.85）不在线误并，留待重聚类校正
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.clustering.online import (
    OUTCOME_ASSIGNED,
    OUTCOME_DUPLICATE,
    OUTCOME_NEW_MICRO,
    OUTCOME_SKIPPED,
    OnlineAssigner,
)
from app.clustering.repository import get_assignment, topic_size
from app.models.topic import Topic
from tests.conftest import make_source
from tests.integration.conftest import make_article

pytestmark = pytest.mark.integration

EVENT_A = "The central bank announced a 25 basis point rate cut to support the slowing economy."
EVENT_A_PARAPHRASE = "To prop up the faltering economy, the central bank lowered its policy rate by a quarter percentage point."
EVENT_A_ZH = "央行宣布降息二十五个基点，以应对经济增长放缓的压力。"
EVENT_A_COPY = "The central bank announced a 25 basis point rate cut to support the slowing economy today."
UNRELATED = "The local football team won the championship after a dramatic final match."


def _embed(embedder, article, text):
    article.embedding = embedder.embed([text])[0]


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_new_article_creates_nascent_micro_topic(db, mpnet_embedder):
    source = make_source(db, language="en")
    article = make_article(db, source, title=UNRELATED)
    _embed(mpnet_embedder, article, UNRELATED)
    db.commit()

    outcome = OnlineAssigner().assign(db, article)
    db.commit()

    assert outcome.outcome == OUTCOME_NEW_MICRO
    topic = db.get(Topic, outcome.topic_id)
    assert topic.lifecycle_state == "nascent"  # 孤证微簇保留
    assert topic.centroid is not None and len(topic.centroid) == 1024
    assert topic.keywords  # c-TF-IDF 风格 top 词（降级命名/匹配输入）
    assert topic.naming_method == "ctfidf_fallback"  # LLM 命名前的兜底留痕
    assignment = get_assignment(db, article.id)
    assert assignment is not None and assignment.assign_method == "online"
    assert float(assignment.weight) == 1.0


def test_second_report_joins_via_event_threshold(db, mpnet_embedder):
    source = make_source(db, language="en")
    first = make_article(db, source, title=EVENT_A)
    second = make_article(db, source, title=EVENT_A_PARAPHRASE)
    _embed(mpnet_embedder, first, EVENT_A)
    _embed(mpnet_embedder, second, EVENT_A_PARAPHRASE)
    db.commit()

    sim = _cosine(first.embedding, second.embedding)
    assigner = OnlineAssigner(t_event=sim - 0.02, t_dup=sim + 0.02)  # 实测相似度标定阈值
    first_outcome = assigner.assign(db, first)
    second_outcome = assigner.assign(db, second)
    db.commit()

    assert first_outcome.outcome == OUTCOME_NEW_MICRO
    assert second_outcome.outcome == OUTCOME_ASSIGNED
    assert second_outcome.topic_id == first_outcome.topic_id
    assert second.is_duplicate is False
    topic = db.get(Topic, first_outcome.topic_id)
    assert topic_size(db, topic.id) == 2
    assert topic.lifecycle_state == "forming"  # 孤证获得同伴 → 形成中
    assignment = get_assignment(db, second.id)
    assert float(assignment.weight) == pytest.approx(sim, abs=0.01)


def test_near_identical_report_marked_duplicate(db, mpnet_embedder):
    source = make_source(db, language="en")
    canonical = make_article(db, source, title=EVENT_A)
    _embed(mpnet_embedder, canonical, EVENT_A)
    db.commit()

    assigner = OnlineAssigner(t_event=0.85, t_dup=0.95)
    first_outcome = assigner.assign(db, canonical)  # 原创先到达先建簇（真实到达顺序）
    db.commit()

    copy = make_article(db, source, title=EVENT_A_COPY)  # 转载后到达
    _embed(mpnet_embedder, copy, EVENT_A_COPY)
    db.commit()
    dup_outcome = assigner.assign(db, copy)
    db.commit()

    assert dup_outcome.outcome == OUTCOME_DUPLICATE
    assert copy.is_duplicate is True and copy.canonical_id == canonical.id
    assert dup_outcome.topic_id == first_outcome.topic_id  # 跟风报道共享议题归属（不重复建簇）
    assert topic_size(db, first_outcome.topic_id) == 2


def test_title_fingerprint_fallback_catches_same_title_different_content(db, mpnet_embedder):
    """标题指纹兜底：HNSW 判重漏判时，标题指纹完全一致直接判转载。

    构造：canonical 与 copy 标题完全一致（指纹命中），但正文不同（向量不同，
    使 HNSW 在 t_dup 阈值下不命中）——指纹兜底应仍判 duplicate。
    """
    source = make_source(db, language="en")
    # 同一标题，正文差异大 → 指纹相同、向量不同
    canonical = make_article(db, source, title="Breaking: Major Policy Shift")
    _embed(mpnet_embedder, canonical, "Breaking: Major Policy Shift announced today by officials.")
    db.commit()

    assigner = OnlineAssigner(t_event=0.99, t_dup=0.99)  # 高阈值：向量判重必不命中
    canonical_outcome = assigner.assign(db, canonical)
    db.commit()

    copy = make_article(db, source, title="Breaking: Major Policy Shift")  # 标题完全一致
    _embed(mpnet_embedder, copy, "Completely unrelated content about a football match result.")
    db.commit()
    dup_outcome = assigner.assign(db, copy)
    db.commit()

    assert dup_outcome.outcome == OUTCOME_DUPLICATE
    assert copy.is_duplicate is True and copy.canonical_id == canonical.id
    assert dup_outcome.topic_id == canonical_outcome.topic_id


def test_crosslingual_pair_not_merged_online_by_default(db, mpnet_embedder):
    """跨语言同事件报道实测相似度低于 T_event=0.85：在线不误并，各自孤证微簇待校正。"""
    source = make_source(db, language="en")
    en = make_article(db, source, title=EVENT_A)
    zh = make_article(db, source, title=EVENT_A_ZH)
    _embed(mpnet_embedder, en, EVENT_A)
    _embed(mpnet_embedder, zh, EVENT_A_ZH)
    db.commit()

    assigner = OnlineAssigner()
    en_outcome = assigner.assign(db, en)
    zh_outcome = assigner.assign(db, zh)
    db.commit()

    assert _cosine(en.embedding, zh.embedding) < 0.85  # 记录实测口径
    assert en_outcome.outcome == OUTCOME_NEW_MICRO
    assert zh_outcome.outcome == OUTCOME_NEW_MICRO
    assert zh_outcome.topic_id != en_outcome.topic_id


def test_assign_is_idempotent_on_redelivery(db, mpnet_embedder):
    source = make_source(db, language="en")
    article = make_article(db, source, title=UNRELATED)
    _embed(mpnet_embedder, article, UNRELATED)
    db.commit()

    assigner = OnlineAssigner()
    first = assigner.assign(db, article)
    second = assigner.assign(db, article)  # worker 重投递
    db.commit()

    assert second.outcome == OUTCOME_SKIPPED
    assert second.topic_id == first.topic_id
    assert topic_size(db, first.topic_id) == 1  # 不重复建归属


def test_online_assign_latency_budget(db, mpnet_embedder):
    """性能口径：在线归簇单篇（判重+质心比对+落库）远低于 5s 预算。"""
    source = make_source(db, language="en")
    articles = [make_article(db, source, title=f"{UNRELATED} ({i})") for i in range(5)]
    for i, article in enumerate(articles):
        _embed(mpnet_embedder, article, f"{UNRELATED} variation {i}")
    db.commit()

    assigner = OnlineAssigner()
    durations = [assigner.assign(db, a).duration_ms for a in articles]
    db.commit()
    p95 = sorted(durations)[int(len(durations) * 0.95) - 1]
    assert p95 < 5000  # 向量化+聚类单篇 P95 ≤5s 目标中归簇部分（实测毫秒级）
    print(f"\n在线归簇单篇耗时 ms: {[f'{d:.1f}' for d in durations]}")


def test_assign_historical_timeline_with_injected_now(db, mpnet_embedder):
    """M5 回放根因回归：历史时间轴文章注入 now=published_at 后，
    活跃议题窗口沿案例时间轴计算，次日报道可命中首日议题（缺省墙钟则全部不可见）。"""
    hist1 = datetime(2021, 3, 24, 10, tzinfo=UTC)
    hist2 = datetime(2021, 3, 25, 12, tzinfo=UTC)
    source = make_source(db, language="en")
    first = make_article(db, source, title=EVENT_A, published_at=hist1)
    second = make_article(db, source, title=EVENT_A_PARAPHRASE, published_at=hist2)
    _embed(mpnet_embedder, first, EVENT_A)
    _embed(mpnet_embedder, second, EVENT_A_PARAPHRASE)
    db.commit()

    sim = _cosine(first.embedding, second.embedding)
    assigner = OnlineAssigner(t_event=sim - 0.02, t_dup=sim + 0.02)  # 实测相似度标定阈值
    first_outcome = assigner.assign(db, first, now=first.published_at)
    second_outcome = assigner.assign(db, second, now=second.published_at)
    db.commit()

    assert first_outcome.outcome == OUTCOME_NEW_MICRO
    assert second_outcome.outcome == OUTCOME_ASSIGNED
    assert second_outcome.topic_id == first_outcome.topic_id
    # 议题时间戳留在案例时间轴上（不被墙钟污染），供次日归并窗口对齐
    topic = db.get(Topic, first_outcome.topic_id)
    assert topic.last_seen_at == hist2


def test_nearest_active_topic_window_with_injected_now(db):
    """活跃窗口时间基准可注入：同一历史议题，墙钟基准不可见、注入基准可见。"""
    from app.clustering.repository import nearest_active_topic

    hist = datetime(2021, 3, 24, 10, tzinfo=UTC)
    vec = [0.0] * 1024
    vec[0] = 1.0
    topic = Topic(
        name="历史议题", name_auto="历史议题", naming_method="ctfidf_fallback",
        cluster_method="agglomerative", keywords=["测试"], country_scope=["US"],
        lifecycle_state="nascent", centroid=vec,
        first_seen_at=hist, last_seen_at=hist,
    )
    db.add(topic)
    db.commit()

    assert nearest_active_topic(db, vec, min_score=0.5) is None  # 墙钟窗口外
    hit = nearest_active_topic(db, vec, min_score=0.5, now=hist + timedelta(hours=2))
    assert hit is not None and hit[0].id == topic.id
