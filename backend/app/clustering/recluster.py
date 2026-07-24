"""每小时全局重聚类校正（T2.9）+ 快照发布 + 降级链编排（T2.11 入口）。

流程（详细设计 4.2 算法 2 注释 / 缓存层规范）：
  ① 读 Redis 降级旗标：降级期恢复后首轮校正窗口自动扩展覆盖降级起始时刻（回填）
  ② 加载近 24h 窗已向量化文章
  ③ 双策略并行评估：Agglomerative 恒跑（对照 + 护栏回落源），BERTopic 主线
     - BERTopic 拟合失败/超大簇黑洞 → 回落 Agglomerative 结果（护栏，WARN 留痕）
     - 双策略均不可用 → 关键词匹配粗聚类降级：cluster_method=keyword_fallback
       + P1 告警 + 降级旗标（T2.11，绝不静默降级）
  ④ 校正落库：簇质心与活跃议题比对复用（≥T_event）或新建；文章迁移留
     assign_method=recluster 痕迹；噪声撤归属回"未归类"池；空壳议题归档
  ⑤ "未归类"池滞留超 48h 文章按关键词粗分（T2.10）
  ⑥ 发布新快照（读侧此前一直读上一版并标注"校正中"）
"""
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import redis as redis_lib
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clustering.agglomerative import AgglomerativeClusterer
from app.clustering.bertopic_cluster import BertopicClusterer
from app.clustering.config import get_cluster_settings
from app.clustering.fallback import (
    assign_by_keywords,
    clear_degraded,
    degraded_since,
    run_fallback,
)
from app.clustering.repository import (
    active_topics,
    archive_empty_topics,
    create_topic,
    lifecycle_for_size,
    load_window_docs,
    move_assignment,
    nearest_active_topic,
    representative_titles,
    topic_size,
    unassign_article,
    unclassified_articles,
)
from app.clustering.snapshot import mark_correcting, mark_ready, publish_snapshot
from app.clustering.types import ClusterDoc, StrategyResult
from app.core.logging import get_logger
from app.models.article import Article

logger = get_logger("clustering.recluster")


@dataclass
class ReclusterReport:
    skipped: bool = False
    skip_reason: str = ""
    method: str = ""
    guardrail_triggered: bool = False  # BERTopic 退化回落 Agglomerative
    degraded: bool = False             # 双策略均不可用，关键词兜底接管
    backfilled: bool = False           # 本轮覆盖了降级窗口并完成回填
    window_docs: int = 0
    clusters_before: int = 0
    clusters_after: int = 0
    reused_topics: int = 0   # merges：校正簇并入既有议题
    new_topics: int = 0
    moved_articles: int = 0  # splits：跨议题迁移纠正
    pooled_articles: int = 0
    stale_coarse_split: dict[str, int] = field(default_factory=dict)
    archived_topics: int = 0
    singletons: int = 0
    noise: int = 0
    largest_share: float = 0.0
    duration_ms: float = 0.0


class ReclusterJob:
    def __init__(
        self,
        bertopic: BertopicClusterer | None = None,
        agglomerative: AgglomerativeClusterer | None = None,
    ):
        self.settings = get_cluster_settings()
        self.bertopic = bertopic or BertopicClusterer()
        self.agglomerative = agglomerative or AgglomerativeClusterer()

    def run(self, db: Session, redis_client: redis_lib.Redis | None = None) -> ReclusterReport:
        t0 = time.perf_counter()
        report = ReclusterReport()

        # ① 降级旗标：恢复后首轮校正窗口扩展至降级起始时刻（回填降级期文章）
        degraded_from = degraded_since(redis_client) if redis_client is not None else None
        normal_cutoff = datetime.now(UTC) - timedelta(hours=self.settings.recluster_window_hours)
        since = min(normal_cutoff, degraded_from) if degraded_from is not None else None
        docs = load_window_docs(db, self.settings.recluster_window_hours, since=since)
        report.window_docs = len(docs)
        if len(docs) < self.settings.recluster_min_docs:
            report.skipped = True
            report.skip_reason = f"窗口文章 {len(docs)} 篇 < 最小样本 {self.settings.recluster_min_docs}"
            logger.info("recluster_skipped", reason=report.skip_reason)
            return report

        if redis_client is not None:
            mark_correcting(redis_client)
        try:
            chosen, agglom_result, report.guardrail_triggered = self._run_strategies(docs)
            if chosen is None:
                # 双策略均不可用 → 关键词匹配降级（T2.11）
                self._run_degraded(db, redis_client, docs, report)
            else:
                self._persist(db, docs, chosen, report)
                report.method = chosen.method
                report.singletons = chosen.singleton_count
                report.noise = len(chosen.noise_indices)
                report.largest_share = chosen.largest_share
                if degraded_from is not None and redis_client is not None:
                    clear_degraded(redis_client)
                    report.backfilled = True
                    logger.info(
                        "cluster_fallback_backfill_done",
                        degraded_since=degraded_from.isoformat(), window_docs=len(docs),
                    )
                self._coarse_split_stale_pool(db, report)
                report.archived_topics = archive_empty_topics(db)
                db.commit()
                if redis_client is not None:
                    publish_snapshot(redis_client, self._build_snapshot(db, docs, chosen, agglom_result, report))
        except Exception:
            db.rollback()
            raise
        finally:
            if redis_client is not None:
                mark_ready(redis_client)

        report.duration_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "recluster_done",
            window_hours=self.settings.recluster_window_hours,
            window_docs=report.window_docs,
            clusters_before=report.clusters_before, clusters_after=report.clusters_after,
            merges=report.reused_topics, splits=report.moved_articles,
            method=report.method, guardrail=report.guardrail_triggered, degraded=report.degraded,
            pooled=report.pooled_articles, archived=report.archived_topics,
            duration_ms=round(report.duration_ms, 1),
        )
        return report

    def _run_strategies(
        self, docs: list[ClusterDoc]
    ) -> tuple[StrategyResult | None, StrategyResult | None, bool]:
        """双策略并行评估：返回 (采用结果, Agglomerative 对照结果, 是否触发护栏)。"""
        agglom_result: StrategyResult | None = None
        try:
            agglom_result = self.agglomerative.cluster(docs)
        except Exception as exc:  # noqa: BLE001 对照策略失败不直接致命，走降级判定
            logger.error("agglomerative_cluster_fail", error=str(exc)[:300])
        try:
            bertopic_result = self.bertopic.cluster(docs)
            return bertopic_result, agglom_result, False
        except Exception as exc:  # noqa: BLE001 BERTopic 不可用/退化 → 护栏回落 Agglomerative
            logger.warning(
                "bertopic_guardrail_fallback", component="nlp_pipeline",
                fallback="agglomerative", reason=str(exc)[:300],
            )
            return agglom_result, agglom_result, True

    def _persist(self, db: Session, docs: list[ClusterDoc], result: StrategyResult, report: ReclusterReport) -> None:
        report.clusters_before = len(active_topics(db))
        now = datetime.now(UTC)
        for cluster in result.clusters:
            members = [docs[i] for i in cluster.member_indices]
            hit = nearest_active_topic(db, cluster.centroid, min_score=self.settings.t_event)
            if hit is not None:
                topic, _ = hit
                topic.keywords = cluster.keywords
                report.reused_topics += 1
            else:
                topic = create_topic(
                    db,
                    name_auto=members[0].title,  # 窗内时间升序，首位即最早报道（首发锚点）
                    keywords=cluster.keywords,
                    cluster_method=result.method,
                    centroid=cluster.centroid,
                    country_scope=[m.country_code for m in members],
                    lifecycle_state=lifecycle_for_size(cluster.size),
                    first_seen_at=members[0].published_at,
                    last_seen_at=now,
                )
                report.new_topics += 1
            countries = set(topic.country_scope or [])
            countries.update(m.country_code for m in members)
            topic.country_scope = sorted(countries)
            topic.centroid = cluster.centroid  # 校正以本轮窗内质心为准（已是加权池化对象）
            topic.last_seen_at = now
            for member in members:
                weight = sum(a * b for a, b in zip(member.embedding, cluster.centroid, strict=True))
                if move_assignment(db, member.article_id, topic, weight, "recluster"):
                    report.moved_articles += 1
            topic.lifecycle_state = lifecycle_for_size(topic_size(db, topic.id))
            db.flush()
        for idx in result.noise_indices:
            if unassign_article(db, docs[idx].article_id):
                report.pooled_articles += 1
        db.flush()
        report.clusters_after = len(active_topics(db))

    def _run_degraded(
        self,
        db: Session,
        redis_client: redis_lib.Redis | None,
        docs: list[ClusterDoc],
        report: ReclusterReport,
    ) -> None:
        reason = "BERTopic 与 Agglomerative 双策略均不可用"
        article_ids = [d.article_id for d in docs]
        articles = list(db.scalars(select(Article).where(Article.id.in_(article_ids))).all())
        stats = run_fallback(db, redis_client, articles, reason)
        db.commit()
        report.method = "keyword_fallback"
        report.degraded = True
        report.stale_coarse_split = stats
        report.duration_ms = 0.0
        logger.warning(
            "recluster_degraded", component="nlp_pipeline", fallback="keyword_fallback",
            reason=reason, window_docs=len(docs), stats=stats,
        )

    def _coarse_split_stale_pool(self, db: Session, report: ReclusterReport) -> None:
        """"未归类"池滞留超 48h 文章按关键词粗分（T2.10；正常粗分非降级，不告警）。"""
        stale = unclassified_articles(db, older_than_hours=self.settings.unclassified_ttl_hours)
        if not stale:
            return
        stats = assign_by_keywords(db, stale, create_topics=True)
        report.stale_coarse_split = stats
        logger.info("unclassified_pool_coarse_split", stale=len(stale), stats=stats)

    def _build_snapshot(
        self,
        db: Session,
        docs: list[ClusterDoc],
        chosen: StrategyResult,
        agglom_result: StrategyResult | None,
        report: ReclusterReport,
    ) -> dict:
        def strategy_metrics(r: StrategyResult | None) -> dict | None:
            if r is None:
                return None
            return {
                "clusters": len(r.clusters), "noise": len(r.noise_indices),
                "singletons": r.singleton_count, "largest_share": round(r.largest_share, 3),
                "duration_ms": round(r.duration_ms, 1),
            }

        # 本轮触达的议题（按规模降序），供看板读侧直接渲染
        touched = sorted(
            (t for t in active_topics(db) if t.last_seen_at is not None),
            key=lambda t: topic_size(db, t.id), reverse=True,
        )[:50]
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "window_hours": self.settings.recluster_window_hours,
            "window_docs": len(docs),
            "method": chosen.method,
            "guardrail_triggered": report.guardrail_triggered,
            "comparison": {
                "bertopic": strategy_metrics(chosen) if chosen.method == "bertopic" else None,
                "agglomerative": strategy_metrics(agglom_result),
            },
            "topics": [
                {
                    "topic_id": str(t.id),
                    "name": t.name,
                    "lifecycle_state": t.lifecycle_state,
                    "cluster_method": t.cluster_method,
                    "size": topic_size(db, t.id),
                    "keywords": t.keywords,
                    "countries": t.country_scope,
                    "representative_titles": representative_titles(
                        db, t.id, self.settings.representative_titles_n
                    ),
                }
                for t in touched
            ],
        }
