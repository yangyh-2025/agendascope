"""命名 worker 集成测试：聚类待命名议题 → LLMTaskQueue → annotator → record_llm_naming 回填。

- 端到端真实链路：跨语言文章 → 真实 mpnet 向量化 → 在线归簇 → 真实 Qwen2.5-0.5B
  命名/分类/摘要回填 topics（模型缺失时跳过；本地开发与验收必须实际跑通，禁止 Mock）
- 降级链：模型目录缺失（真实加载失败，非 Mock）→ ctfidf_fallback 兜底标签 + P1 告警
  + llm_judgements 失败留痕，绝不静默；恢复探针失败时不重复处置、告警防抖
"""
import time

import pytest
from sqlalchemy import func, select

from app.clustering.online import OnlineAssigner
from app.clustering.repository import get_assignment
from app.llm.annotator import TopicAnnotator
from app.llm.prompts import DEFAULT_CATEGORIES
from app.llm.settings import LLMSettings
from app.models.alert import Alert
from app.models.llm import LLMJudgement
from app.models.topic import Topic
from app.worker.naming_worker import NamingWorker
from tests.conftest import make_source
from tests.integration.conftest import make_article

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# 同一事件的中英跨语言报道（应归同一议题簇）
ARTICLES = [
    ("zh", "俄乌双方就停火协议展开新一轮谈判"),
    ("zh", "俄乌谈判在伊斯坦布尔重启 停火成焦点"),
    ("zh", "多方斡旋推动俄乌停火谈判取得进展"),
    ("en", "Kremlin confirms new round of ceasefire talks with Ukraine"),
    ("en", "Ukraine and Russia exchange views on ceasefire terms in Istanbul"),
]


@pytest.fixture(scope="session")
def real_annotator():
    settings = LLMSettings()
    annotator = TopicAnnotator(settings=settings)
    if not annotator.engine.model_dir_exists():
        pytest.skip(f"模型未下载: {settings.resolved_model_dir()}（需先下载 Qwen2.5-0.5B-Instruct）")
    annotator.engine.load()
    return annotator


def _build_pending_topics(db, mpnet_embedder, t_event: float = 0.6) -> list:
    """构造跨语言文章 → 真实向量化 → 在线归簇，返回归簇后的文章列表。

    t_event 降至 0.6：M2-1 实测同事件中英报道对 cosine 约 0.6+，默认 0.85
    会把跨语言同事件报道碎成单篇微簇，无法验证多文章簇的命名链路。
    """
    source_zh = make_source(db, language="zh", country_code="CN")
    source_en = make_source(db, language="en")
    assigner = OnlineAssigner(t_event=t_event)
    articles = []
    for language, title in ARTICLES:
        source = source_zh if language == "zh" else source_en
        article = make_article(db, source, title=title, language=language)
        article.embedding = mpnet_embedder.embed([title])[0]
        db.flush()
        assigner.assign(db, article)
        articles.append(article)
    db.commit()
    return articles


async def test_no_pending_returns_zero(db, redis_client):
    """无待命名议题：空转返回 0，不触碰推理引擎。"""
    worker = NamingWorker(
        annotator=TopicAnnotator(settings=LLMSettings(model_dir="models/__nonexistent__")),
        redis_client=redis_client,
    )
    try:
        assert await worker.run_once() == 0
    finally:
        await worker.llm_queue.stop()


async def test_end_to_end_naming_pipeline(db, redis_client, mpnet_embedder, real_annotator):
    """全链路：跨语言文章 → 向量化 → 归簇 → LLM 命名/分类/摘要回填 topics + llm_judgements 留痕。"""
    articles = _build_pending_topics(db, mpnet_embedder)
    main_assignment = get_assignment(db, articles[0].id)
    assert main_assignment is not None

    worker = NamingWorker(annotator=real_annotator, redis_client=redis_client)
    started = time.monotonic()
    try:
        named = await worker.run_once()
    finally:
        elapsed = time.monotonic() - started
        await worker.llm_queue.stop()
    print(f"\n[实测] 命名 worker 单轮回填 {named} 个议题，耗时 {elapsed:.1f}s（含引擎已在会话内加载）")
    assert named >= 1, "至少主议题应以 LLM 结果回填"

    db.expire_all()
    topic = db.get(Topic, main_assignment.topic_id)
    assert topic.naming_method == "llm"
    assert not topic.name.startswith("关键词:"), "LLM 命名不得是兜底标签"
    assert topic.name != articles[0].title, "LLM 命名应是归纳后的议题名，不是照抄标题"
    assert 2 <= len(topic.name) <= 60
    assert topic.topic_category in DEFAULT_CATEGORIES
    assert topic.summary_zh and any("一" <= ch <= "鿿" for ch in topic.summary_zh), "摘要必须是中文"
    assert topic.llm_model == "Qwen2.5-0.5B-Instruct"
    assert topic.prompt_version == "topic-naming-v1"

    judgements = db.scalars(
        select(LLMJudgement).where(LLMJudgement.topic_id == topic.id)
    ).all()
    task_types = {j.task_type for j in judgements}
    assert {"topic_naming", "topic_category", "topic_summary"} <= task_types, "三条判定必须逐条留痕"
    assert all(j.success for j in judgements if j.topic_id == topic.id)
    print(f"[实测] 议题名「{topic.name}」/ 分类「{topic.topic_category}」/ 摘要「{topic.summary_zh}」")


async def test_degraded_fallback_not_silent(db, redis_client, mpnet_embedder):
    """模型目录缺失（真实加载失败）：ctfidf_fallback 兜底 + P1 告警 + 失败留痕，不静默。"""
    articles = _build_pending_topics(db, mpnet_embedder)
    settings = LLMSettings(model_dir="models/__nonexistent_qwen__")
    annotator = TopicAnnotator(settings=settings)
    worker = NamingWorker(annotator=annotator, redis_client=redis_client)
    try:
        named = await worker.run_once()
    finally:
        await worker.llm_queue.stop()
    assert named == 0
    assert annotator.monitor.degraded, "模型加载失败必须判降级"

    db.expire_all()
    topic = db.get(Topic, get_assignment(db, articles[0].id).topic_id)
    assert topic.naming_method == "ctfidf_fallback", "降级议题保持兜底命名留痕，等待恢复后回填"
    assert topic.name.startswith("关键词:"), "兜底标签显式「关键词：」前缀，不伪装 LLM 命名"

    judgements = db.scalars(
        select(LLMJudgement).where(LLMJudgement.topic_id == topic.id)
    ).all()
    assert judgements and all(not j.success for j in judgements), "降级判定必须失败留痕"
    assert all(j.naming_method == "ctfidf_fallback" for j in judgements)

    alerts = db.scalars(select(Alert)).all()
    llm_alerts = [a for a in alerts if a.payload.get("kind") == "llm_degraded"]
    assert len(llm_alerts) == 1 and llm_alerts[0].payload["severity"] == "P1", "降级必须写 P1 告警"

    # 第二轮：恢复探针仍失败（模型目录仍缺失）→ 不批量处置、不重复写告警（防抖）
    try:
        assert await worker.run_once() == 0
    finally:
        await worker.llm_queue.stop()
    db.expire_all()
    remaining = db.scalar(select(func.count()).select_from(Alert))
    assert remaining == len(alerts), "探针失败不得重复刷告警"
