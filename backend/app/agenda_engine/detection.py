"""事件检测编排器（T3.6-T3.12 主链路串联，详细设计 4.2 算法 4 detect_origin）。

背景：detect_media_origin / compute_follower_sequence / compute_stats_evidence /
evaluate_conditions / upsert_event / review_event / echo_fold_topic /
judge_first_utterance / find_or_create_entity 原本是互不相调用的孤岛函数，
本模块把它们串成完整生产链路，由 app.worker.detection_worker 周期驱动。

对活跃议题（merged_into IS NULL 且 lifecycle_state != 'archived'，按 last_seen_at
降序取 detection_topic_batch_size 个）逐议题执行：

  1. 回声消除折叠 echo_fold_topic（T3.1，落库 is_duplicate/canonical_id）
  2. 实体登记：议题内报道 NER 提及 → find_or_create_entity 入 persons_orgs（T3.7）。
     轻量 NER 只能可靠识别人名，仅 PEOPLE→person 自动登记；ORG/LOCATION/OTHER
     类型判定不可靠不自动登记（记 debug 日志跳过），黑名单实体不登记；
  3. 首发锚点判定 detect_media_origin（T3.6，通讯社 boost 倾斜）
  4. LLM 首发表述判定 judge_first_utterance（T3.8）：候选 = 回声节点 canonical
     报道中的已登记实体提及（needs_review 提及不进判定，先人工确认实体身份）；
     LLM 不可用/降级 → detection_method='media_time_fallback' 回落
     （详细设计 4.2 算法 4 注释：仅媒体时间锚点，置信度降一级），
     并写 LLM 降级 P1 告警（write_llm_degraded_alert，Redis 防抖）
  5. 跟随国序列 compute_follower_sequence（T3.9）
  6. 统计佐证 compute_stats_evidence（T3.10，有跟随国时；样本 <100 硬性拒绝）
  7. 事件判定 evaluate_conditions → upsert_event（T3.11）
  8. LLM 终审 review_event（T3.12）：终审不可用直进人工复核队列不自动告警
     （final_review.verdict='skipped_unavailable'，review_event 内部已处理）

事务与容错：run_detection_cycle 按议题独立 commit/rollback——单议题失败记
error 日志后回滚并继续下一议题，不阻塞整轮；绝不静默兜底：所有降级路径均有
字段标记（detection_method='media_time_fallback' / final_review.verdict）+
结构化日志 + P1 告警（LLM 降级时）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.config import AgendaSettings, get_agenda_settings
from app.agenda_engine.echo import EchoNode, echo_fold_topic
from app.agenda_engine.entity_blacklist import is_blacklisted
from app.agenda_engine.entity_extract import extract_entities, is_valid_entity
from app.agenda_engine.entity_repo import find_or_create_entity, match_entities_in_text
from app.agenda_engine.event import (
    EventDetectionInput,
    evaluate_conditions,
    upsert_event,
)
from app.agenda_engine.final_review import review_event
from app.agenda_engine.first_utterance import (
    DETECTION_METHOD_FALLBACK,
    judge_first_utterance,
)
from app.agenda_engine.origin import (
    MediaOrigin,
    compute_follower_sequence,
    detect_media_origin,
)
from app.agenda_engine.stats_evidence import StatsEvidence, compute_stats_evidence
from app.core.logging import get_logger
from app.models.article import Article
from app.models.topic import Topic, TopicArticle

if TYPE_CHECKING:
    import redis

logger = get_logger("agenda_engine.detection")

DetectionMethod = Literal["llm", "media_time_fallback"]

# 媒体时间回落时的置信度降一级映射（详细设计 4.2 算法 4 注释："置信度降一级"）
_FALLBACK_CONFIDENCE_DOWNGRADE: dict[str, str] = {"high": "medium", "medium": "low", "low": "low"}


@dataclass
class TopicDetectionResult:
    """单议题检测链路执行结果（全部阶段留痕，供 worker 观测与测试断言）。"""

    topic_id: UUID
    echo_nodes: int = 0
    echo_folded: int = 0
    entities_registered: int = 0
    media_origin_found: bool = False
    person_origin_entity_id: UUID | None = None
    detection_method: DetectionMethod = "media_time_fallback"
    first_utterance_judges: int = 0
    follower_count: int = 0
    stats_insufficient: bool | None = None
    event_id: UUID | None = None
    event_status: str | None = None
    final_review_verdict: str | None = None
    skipped_reason: str | None = None
    error: str | None = None


@dataclass
class DetectionReport:
    """一轮检测周期报告。"""

    scanned: int = 0
    events_created: int = 0
    events_reviewed: int = 0
    fallback_topics: int = 0  # media_time_fallback 回落的议题数
    failed_topics: list[UUID] = field(default_factory=list)
    results: list[TopicDetectionResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 阶段 2：实体登记（NER 提及 → find_or_create_entity）
# ---------------------------------------------------------------------------
def _register_topic_entities(
    db: Session,
    topic_id: UUID,
    *,
    redis_client: redis.Redis | None,
    settings: AgendaSettings,
) -> int:
    """对议题内报道跑轻量 NER，把人名实体登记进 persons_orgs（find_or_create_entity 查重）。

    口径与限制：
      - 仅 PEOPLE→'person'：jieba/规则 NER 对人名召回可靠；ORG 无法区分
        智库/国际组织/政府机构（persons_orgs.entity_type CHECK 四类），
        自动登记会误标类型污染实体库，故跳过（已由人工/种子数据登记的实体
        仍可通过 match_entities_in_text 命中参与首发判定）；
      - 命中 entity:blacklist 的实体不登记（防超级节点灌入实体库）；
      - 单议题单轮上限 detection_entity_register_limit；
      - 返回本轮 find_or_create_entity 调用次数（含命中已有实体的查重）。
    """
    cutoff = datetime.now(UTC) - timedelta(days=settings.echo_lookback_days)
    stmt = (
        select(Article)
        .join(TopicArticle, TopicArticle.article_id == Article.id)
        .where(
            TopicArticle.topic_id == topic_id,
            Article.published_at >= cutoff,
        )
        .order_by(Article.published_at.asc())
    )
    registered = 0
    seen: set[str] = set()
    for article in db.scalars(stmt).all():
        if registered >= settings.detection_entity_register_limit:
            break
        text = f"{article.title or ''}\n{article.content or ''}".strip()
        if not text:
            continue
        for entity_text, kind in extract_entities(text):
            if registered >= settings.detection_entity_register_limit:
                break
            if kind != "PEOPLE":
                continue  # ORG/LOCATION/OTHER 类型不可靠，不自动登记（见 docstring）
            if not is_valid_entity(entity_text):
                continue
            key = entity_text.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            if redis_client is not None and is_blacklisted(entity_text, redis_client):
                logger.info(
                    "detection_entity_skip_blacklisted",
                    topic_id=str(topic_id), entity=entity_text,
                )
                continue
            find_or_create_entity(
                db,
                name=entity_text,
                entity_type="person",
                country_code=article.country_code,
            )
            registered += 1
    if registered:
        logger.info(
            "detection_entities_registered",
            topic_id=str(topic_id), registered=registered,
        )
    return registered


# ---------------------------------------------------------------------------
# 阶段 4：LLM 首发表述判定（含 media_time_fallback 回落）
# ---------------------------------------------------------------------------
def _judge_person_origin(
    db: Session,
    topic_id: UUID,
    nodes: list[EchoNode],
    *,
    llm_annotator: Any,
    redis_client: redis.Redis | None,
    settings: AgendaSettings,
) -> tuple[UUID | None, str | None, int]:
    """对回声节点 canonical 报道中的实体提及逐个跑 LLM 首发判定。

    返回 (person_origin_entity_id, origin_quote, judges)；无有效首发判定返回
    (None, None, judges)。needs_review 的提及（同名歧义低置信）不进判定——
    先人工确认实体身份（T3.7/T3.8 口径）。单议题判定上限
    detection_max_first_utterance_judges（控制 LLM 成本）。
    """
    judges = 0
    for node in nodes:
        article = db.get(Article, node.canonical_article_id)
        if article is None:
            continue
        text = f"{article.title or ''}\n{article.content or ''}".strip()
        if not text:
            continue
        mentions = match_entities_in_text(
            db, text, redis_client=redis_client, settings=settings,
        )
        for mention in mentions:
            if mention.needs_review:
                continue
            if judges >= settings.detection_max_first_utterance_judges:
                return None, None, judges
            judges += 1
            verdict = judge_first_utterance(
                db,
                topic_id,
                mention.entity_id,
                article.id,
                llm_annotator,
                settings=settings,
            )
            if verdict is not None and verdict.is_first_utterance:
                logger.info(
                    "detection_person_origin_found",
                    topic_id=str(topic_id),
                    entity_id=str(verdict.entity_id),
                    article_id=str(article.id),
                )
                return verdict.entity_id, verdict.evidence_quote, judges
    return None, None, judges


def _apply_media_time_fallback(origin: MediaOrigin) -> MediaOrigin:
    """LLM 不可用时的媒体时间回落（详细设计 4.2 算法 4 注释）：仅媒体时间锚点，
    置信度降一级（high→medium、medium→low）。返回降级后的 MediaOrigin 副本，
    不改写原始判定对象。
    """
    downgraded = _FALLBACK_CONFIDENCE_DOWNGRADE.get(origin.confidence, origin.confidence)
    if downgraded == origin.confidence:
        return origin
    return replace(origin, confidence=downgraded)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 单议题主链路
# ---------------------------------------------------------------------------
def detect_topic_event(
    db: Session,
    topic_id: UUID,
    *,
    llm_annotator: Any = None,
    redis_client: redis.Redis | None = None,
    settings: AgendaSettings | None = None,
) -> TopicDetectionResult:
    """对单个活跃议题串起完整事件检测链路（flush 由本函数完成；commit 由调用方负责）。

    llm_annotator：TopicAnnotator 实例（依赖注入）；None 或 monitor.degraded 时
    走 media_time_fallback 回落（首发判定降级），终审直进人工复核队列。
    """
    cfg = settings or get_agenda_settings()
    result = TopicDetectionResult(topic_id=topic_id)

    topic = db.get(Topic, topic_id)
    if topic is None:
        result.skipped_reason = "topic_not_found"
        return result

    # 1. 回声消除折叠（落库 is_duplicate/canonical_id）
    nodes = echo_fold_topic(db, topic_id)
    result.echo_nodes = len(nodes)
    result.echo_folded = sum(len(n.related_docs) for n in nodes)

    # 2. 实体登记（NER 提及 → persons_orgs）
    result.entities_registered = _register_topic_entities(
        db, topic_id, redis_client=redis_client, settings=cfg,
    )

    # 3. 首发锚点判定
    media_origin = detect_media_origin(db, topic_id)
    if media_origin is None:
        result.skipped_reason = "no_original_articles"
        logger.info("detection_skip_no_origin", topic_id=str(topic_id))
        return result
    result.media_origin_found = True

    # 4. LLM 首发表述判定（含降级回落）
    llm_available = llm_annotator is not None and not llm_annotator.monitor.degraded
    person_origin_entity_id: UUID | None = None
    origin_quote: str | None = None
    if llm_available:
        person_origin_entity_id, origin_quote, result.first_utterance_judges = (
            _judge_person_origin(
                db, topic_id, nodes,
                llm_annotator=llm_annotator,
                redis_client=redis_client,
                settings=cfg,
            )
        )
        # LLM 参与了判定（无论是否命中人物首发）→ 'llm'
        result.detection_method = "llm"
    else:
        # media_time_fallback 回落：仅媒体时间锚点，置信度降一级
        result.detection_method = DETECTION_METHOD_FALLBACK
        reason = (
            "llm_annotator 未注入"
            if llm_annotator is None
            else (llm_annotator.monitor.reason or "llm_service 降级")
        )
        logger.warning(
            "detection_media_time_fallback",
            topic_id=str(topic_id), reason=reason,
        )

    effective_origin = media_origin
    if result.detection_method == DETECTION_METHOD_FALLBACK:
        effective_origin = _apply_media_time_fallback(media_origin)

    result.person_origin_entity_id = person_origin_entity_id

    # 5. 跟随国序列
    followers = compute_follower_sequence(db, topic_id, media_origin)
    result.follower_count = len(followers)

    # 6. 统计佐证（有跟随国才计算；样本 <100 由 stats_evidence 硬性拒绝）
    stats: StatsEvidence | None = None
    if followers:
        stats = compute_stats_evidence(
            db,
            topic_id,
            origin_country=media_origin.country_code,
            follower_countries=[f.country_code for f in followers],
            window_days=cfg.stats_window_days,
        )
        result.stats_insufficient = stats.insufficient_data

    # 7. 事件判定
    input_data = EventDetectionInput(
        topic_id=topic_id,
        media_origin=effective_origin,
        person_origin_entity_id=person_origin_entity_id,
        origin_quote=origin_quote,
        followers=followers,
        stats=stats,
        detection_method=result.detection_method,
    )
    decision = evaluate_conditions(db, input_data)
    event = upsert_event(db, input_data, decision)
    if event is None:
        result.skipped_reason = decision.reason
        return result
    result.event_id = event.id
    result.event_status = event.status

    # 8. LLM 终审（终审不可用直进人工复核队列，不自动告警；
    #    已有 completed/rejected 结论的事件不重复终审，skipped_unavailable 下轮重试）
    existing_verdict = (event.final_review or {}).get("verdict")
    if event.status == "suspected" and existing_verdict not in ("completed", "rejected"):
        if llm_annotator is not None:
            review = review_event(db, event, topic_name=topic.name, llm_annotator=llm_annotator)
            result.final_review_verdict = review.verdict
        else:
            # 终审未配置：与 review_event 降级路径同口径标记，直进人工复核队列
            event.final_review = {
                "score": None,
                "verdict": "skipped_unavailable",
                "model": None,
                "prompt_version": "final-review-v1",
                "reviewed_at": datetime.now(UTC).isoformat(),
                "reasoning": "终审不可用：llm_annotator 未注入",
                "concerns": [],
            }
            db.flush()
            result.final_review_verdict = "skipped_unavailable"
            logger.warning(
                "detection_final_review_unavailable",
                topic_id=str(topic_id), event_id=str(event.id),
            )
    elif event.final_review:
        result.final_review_verdict = existing_verdict

    result.event_status = event.status
    db.flush()
    logger.info(
        "detection_topic_done",
        topic_id=str(topic_id),
        detection_method=result.detection_method,
        event_id=str(event.id),
        event_status=event.status,
        final_review_verdict=result.final_review_verdict,
    )
    return result


# ---------------------------------------------------------------------------
# 周期入口（worker 驱动）
# ---------------------------------------------------------------------------
def _active_detection_topic_ids(db: Session, limit: int) -> list[UUID]:
    """参与本轮检测的活跃议题：未归档未并入，按 last_seen_at 降序取前 limit 个。"""
    stmt = (
        select(Topic.id)
        .where(
            Topic.merged_into.is_(None),
            Topic.lifecycle_state != "archived",
        )
        .order_by(Topic.last_seen_at.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def run_detection_cycle(
    db: Session,
    *,
    llm_annotator: Any = None,
    redis_client: redis.Redis | None = None,
    settings: AgendaSettings | None = None,
    topic_ids: list[UUID] | None = None,
) -> DetectionReport:
    """对活跃议题跑一轮完整事件检测（worker 周期入口）。

    事务边界：按议题独立 commit/rollback——单议题异常回滚该议题并记 error
    后继续，不污染其他议题的落库结果。
    LLM 降级（monitor.degraded）时整轮走 media_time_fallback，并写一次 P1
    降级告警（write_llm_degraded_alert，Redis 防抖 1h，绝不静默降级）。
    """
    cfg = settings or get_agenda_settings()
    report = DetectionReport()

    ids = topic_ids if topic_ids is not None else _active_detection_topic_ids(
        db, cfg.detection_topic_batch_size
    )
    report.scanned = len(ids)

    llm_degraded = llm_annotator is not None and llm_annotator.monitor.degraded
    if llm_degraded:
        # 降级告警（沿用 T2.16 naming_method/告警模式：字段标记 + P1 告警 + Redis 防抖）
        from app.llm.alerting import write_llm_degraded_alert

        write_llm_degraded_alert(
            db,
            reason=f"事件检测首发判定降级 media_time_fallback：{llm_annotator.monitor.reason or 'llm_service 降级'}",
            since=llm_annotator.monitor.degraded_since,
            redis_client=redis_client,
        )
        db.commit()  # 告警先行落库，议题事务互不污染

    for topic_id in ids:
        try:
            result = detect_topic_event(
                db,
                topic_id,
                llm_annotator=llm_annotator,
                redis_client=redis_client,
                settings=cfg,
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001 单议题失败不阻塞整轮
            db.rollback()
            logger.error(
                "detection_topic_fail",
                topic_id=str(topic_id), error=str(exc)[:300],
            )
            report.failed_topics.append(topic_id)
            continue
        report.results.append(result)
        if result.event_id is not None:
            report.events_created += 1
        if result.final_review_verdict in ("completed", "rejected"):
            report.events_reviewed += 1
        if result.detection_method == DETECTION_METHOD_FALLBACK and result.media_origin_found:
            report.fallback_topics += 1

    logger.info(
        "detection_cycle_done",
        scanned=report.scanned,
        events=report.events_created,
        reviewed=report.events_reviewed,
        fallback_topics=report.fallback_topics,
        failed=len(report.failed_topics),
        llm_degraded=llm_degraded,
    )
    return report


__all__ = [
    "DetectionReport",
    "TopicDetectionResult",
    "detect_topic_event",
    "run_detection_cycle",
]
