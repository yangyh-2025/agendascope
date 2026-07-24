"""聚类产出 service 接口集成测试（供 LLM 服务接线的契约面）。"""
import pytest

from app.clustering.repository import assign_article, create_topic, representative_titles
from app.clustering.service import ClusterService
from app.core.errors import BizError
from tests.conftest import make_source
from tests.integration.conftest import make_article

pytestmark = pytest.mark.integration

TITLES = [
    "央行宣布降息二十五个基点，以应对经济增长放缓的压力。",
    "美联储宣布降息二十五个基点，市场期待更多宽松政策。",
    "降息预期升温，央行声明发布后债券市场走强。",
]


def _make_topic_with_articles(db):
    source = make_source(db, language="zh")
    topic = create_topic(
        db,
        name_auto=TITLES[0],
        keywords=["央行", "降息", "经济"],
        cluster_method="bertopic",
        centroid=None,
        country_scope=["CN"],
        lifecycle_state="confirmed",
    )
    for i, title in enumerate(TITLES):
        article = make_article(db, source, title=title)
        assign_article(db, topic, article.id, weight=1.0 - i * 0.1, assign_method="recluster")
    db.commit()
    return topic


def test_dossier_exposes_cluster_outputs(db):
    topic = _make_topic_with_articles(db)
    service = ClusterService(db)
    dossier = service.get_cluster_dossier(topic.id)

    assert dossier.topic_id == topic.id
    assert dossier.size == 3
    assert dossier.keywords == ["央行", "降息", "经济"]  # c-TF-IDF top 词
    assert dossier.countries == ["CN"]
    assert dossier.representative_titles  # 簇内代表标题（LLM 命名器输入）
    assert dossier.representative_titles[0] == TITLES[0]  # 权重降序
    assert dossier.cluster_method == "bertopic"


def test_list_pending_naming_picks_fallback_topics(db):
    topic = _make_topic_with_articles(db)
    pending = ClusterService(db).list_pending_naming()
    assert [d.topic_id for d in pending] == [topic.id]

    # LLM 回填后不再出现在待命名队列
    ClusterService(db).record_llm_naming(topic.id, name="全球主要央行进入降息周期", topic_category="经济金融", summary_zh="多国央行相继降息。")
    db.commit()
    assert ClusterService(db).list_pending_naming() == []


def test_record_llm_naming_respects_human_lock(db):
    topic = _make_topic_with_articles(db)
    topic.human_locked_fields = ["name"]
    db.commit()

    service = ClusterService(db)
    service.record_llm_naming(topic.id, name="机器命名", topic_category="经济金融")
    db.commit()
    db.expire_all()

    assert topic.name == TITLES[0]  # 人工锁定字段不被机器推翻
    assert topic.naming_method == "ctfidf_fallback"
    assert topic.topic_category == "经济金融"  # 未锁定字段正常回填


def test_get_dossier_missing_topic_raises(db):
    import uuid

    with pytest.raises(BizError):
        ClusterService(db).get_cluster_dossier(uuid.uuid4())


def test_representative_titles_weight_order(db):
    topic = _make_topic_with_articles(db)
    titles = representative_titles(db, topic.id, 2)
    assert titles == TITLES[:2]
