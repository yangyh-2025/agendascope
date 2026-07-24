"""议题命名/分类/摘要编排（T2.13–T2.17）。

职责：
- 单次判定：name_topic / classify_topic / summarize_topic，含降级链与留痕；
- 组合判定：annotate_topic 一次完成命名+分类+摘要并可落库；
- 降级回填：backfill_degraded_topics 对降级期议题恢复后重命名/分类；
- 版本对比：rerun_judgements 用指定 prompt 版本批量重跑历史判定。

留痕（详细设计 3.2 关键不变量③）：每次判定写 llm_judgements 表
（模型名 + prompt_version + 输入/输出快照 + 成败 + 耗时），并同步
topics.llm_model / topics.prompt_version。
"""
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import prompts
from app.llm.alerting import write_llm_degraded_alert
from app.llm.ctfidf import ctfidf_keywords, fallback_label
from app.llm.engine import LLMEngine
from app.llm.errors import LLMError, LLMUnavailableError
from app.llm.health import DegradationMonitor
from app.llm.schemas import CategoryOutput, NamingOutput, SummaryOutput
from app.llm.settings import LLMSettings, get_llm_settings
from app.models.llm import LLMJudgement
from app.models.topic import Topic

logger = structlog.get_logger(__name__)

NAMING_LLM = "llm"
NAMING_FALLBACK = "ctfidf_fallback"


@dataclass
class JudgementResult:
    """单次判定结果（不落库版本）。"""

    task_type: str
    value: Any  # str（命名/摘要/类别）
    naming_method: str  # llm / ctfidf_fallback
    model_name: str
    prompt_version: str
    latency_s: float
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class TopicAnnotation:
    """一次完整议题标注（命名+分类+摘要）。"""

    name: JudgementResult
    category: JudgementResult
    summary: JudgementResult | None
    keywords: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)  # 判定输入快照（代表标题/top 词），留痕与重跑用

    @property
    def degraded(self) -> bool:
        return self.name.naming_method != NAMING_LLM


class TopicAnnotator:
    """议题标注服务。输入的代表标题/top 词由聚类引擎提供（M2-2），本类不负责聚类。"""

    def __init__(
        self,
        engine: LLMEngine | None = None,
        monitor: DegradationMonitor | None = None,
        settings: LLMSettings | None = None,
    ):
        self.settings = settings or get_llm_settings()
        self.engine = engine or LLMEngine(self.settings)
        self.monitor = monitor or DegradationMonitor(self.settings)
        self.categories = prompts.parse_categories(self.settings.categories) or prompts.DEFAULT_CATEGORIES

    # ------------------------------------------------------------------
    # 输入预算
    # ------------------------------------------------------------------
    def _fit_budget(self, titles: list[str], top_words: list[str]) -> list[str]:
        """代表标题裁剪到上下文预算内（≤max_context_tokens，T2.13 估算 2000）。"""
        budget = self.settings.max_context_tokens
        kept: list[str] = []
        used = self.engine.count_tokens("".join(top_words)) + 200  # 预留 system/模板开销
        for title in titles:
            cost = self.engine.count_tokens(title) + 4
            if used + cost > budget:
                break
            kept.append(title)
            used += cost
        return kept or titles[:1]

    # ------------------------------------------------------------------
    # 单次判定
    # ------------------------------------------------------------------
    def _run_task(
        self,
        task_type: str,
        titles: list[str],
        top_words: list[str],
        name: str | None = None,
        prompt_version: str | None = None,
    ) -> JudgementResult:
        template = prompts.get_prompt(
            task_type, version=prompt_version,
            categories=self.categories if task_type == prompts.TASK_CATEGORY else None,
        )
        payload: dict[str, Any] = {"titles": titles, "top_words": top_words, "name": name}
        model_name = self.engine.model_name

        if self.monitor.degraded:
            return self._degraded_result(task_type, titles, top_words, template.version, model_name)

        try:
            if not self.engine.is_loaded:
                self.engine.load()
            user_prompt = template.build_user(payload)
            if task_type == prompts.TASK_NAMING:
                parsed, latency = self.engine.generate_structured(template.system, user_prompt, NamingOutput)
                value: Any = parsed.name
            elif task_type == prompts.TASK_CATEGORY:
                parsed, latency = self.engine.generate_structured(template.system, user_prompt, CategoryOutput)
                value = parsed.category
                if value not in self.categories:
                    # 分类漂移：不在分类体系内按失败处理（重试已在引擎内做过一次）
                    raise LLMError(f"分类结果不在预置体系内: {value}")
            else:
                parsed, latency = self.engine.generate_structured(template.system, user_prompt, SummaryOutput)
                value = parsed.summary
            self.monitor.record(True)
            return JudgementResult(
                task_type=task_type, value=value, naming_method=NAMING_LLM,
                model_name=model_name, prompt_version=template.version, latency_s=latency,
            )
        except LLMUnavailableError as exc:
            flipped = self.monitor.mark_unavailable(str(exc))
            if flipped:
                logger.warning("llm_unavailable_enter_fallback", error=str(exc)[:200])
            return self._degraded_result(
                task_type, titles, top_words, template.version, model_name, error=str(exc)[:300]
            )
        except LLMError as exc:
            # 单点降级（T2.12）：解析重试后仍失败 / 分类漂移 → 该议题走兜底，不拖垮整体
            self.monitor.record(False, reason=str(exc)[:200])
            return self._degraded_result(
                task_type, titles, top_words, template.version, model_name, error=str(exc)[:300]
            )

    def _degraded_result(
        self,
        task_type: str,
        titles: list[str],
        top_words: list[str],
        version: str,
        model_name: str,
        error: str | None = None,
    ) -> JudgementResult:
        if task_type == prompts.TASK_NAMING:
            value: Any = fallback_label(titles, top_words)
        elif task_type == prompts.TASK_CATEGORY:
            value = "其他"
        else:
            value = None  # 摘要不伪造内容，降级期留空，恢复后回填
        return JudgementResult(
            task_type=task_type, value=value, naming_method=NAMING_FALLBACK,
            model_name=model_name, prompt_version=version, latency_s=0.0,
            error=error or "llm_degraded",
        )

    def name_topic(
        self, titles: list[str], top_words: list[str], prompt_version: str | None = None
    ) -> JudgementResult:
        """议题命名（T2.13）：代表标题 5-10 条 + c-TF-IDF top 词 → 议题名。"""
        return self._run_task(prompts.TASK_NAMING, self._fit_budget(titles, top_words), top_words,
                             prompt_version=prompt_version)

    def classify_topic(
        self, titles: list[str], top_words: list[str], name: str | None = None,
        prompt_version: str | None = None,
    ) -> JudgementResult:
        """主题分类（T2.14）：输出必在预置/扩展分类体系内。"""
        return self._run_task(prompts.TASK_CATEGORY, self._fit_budget(titles, top_words), top_words,
                             name=name, prompt_version=prompt_version)

    def summarize_topic(
        self, titles: list[str], top_words: list[str], name: str | None = None,
        prompt_version: str | None = None,
    ) -> JudgementResult:
        """议题摘要（T2.15）：2-3 句中文摘要。"""
        return self._run_task(prompts.TASK_SUMMARY, self._fit_budget(titles, top_words), top_words,
                             name=name, prompt_version=prompt_version)

    # ------------------------------------------------------------------
    # 组合标注 + 落库留痕
    # ------------------------------------------------------------------
    def annotate_topic(self, titles: list[str], top_words: list[str]) -> TopicAnnotation:
        """一次完成命名+分类+摘要（降级时逐项走兜底）。"""
        name = self.name_topic(titles, top_words)
        resolved_name = name.value if isinstance(name.value, str) else None
        category = self.classify_topic(titles, top_words, name=resolved_name)
        summary = self.summarize_topic(titles, top_words, name=resolved_name)
        return TopicAnnotation(
            name=name, category=category, summary=summary,
            keywords=ctfidf_keywords(titles, top_words, limit=20),
            inputs={"titles": list(titles), "top_words": list(top_words)},
        )

    def persist_annotation(
        self,
        db: Session,
        topic: Topic,
        annotation: TopicAnnotation,
        redis_client: Any = None,
    ) -> None:
        """标注结果落 topics 表并逐条写 llm_judgements 留痕；降级时写 P1 告警。"""
        for result in (annotation.name, annotation.category, annotation.summary):
            if result is None:
                continue
            self._record_judgement(db, topic.id, result, {
                **annotation.inputs,
                "keywords": annotation.keywords,
            })

        topic.name_auto = str(annotation.name.value)
        if "name" not in (topic.human_locked_fields or []):
            topic.name = str(annotation.name.value)
        if "topic_category" not in (topic.human_locked_fields or []) and annotation.category.success:
            topic.topic_category = str(annotation.category.value)
        if annotation.summary is not None and annotation.summary.success and annotation.summary.value:
            topic.summary_zh = str(annotation.summary.value)
        topic.naming_method = annotation.name.naming_method
        topic.keywords = annotation.keywords
        topic.llm_model = annotation.name.model_name
        topic.prompt_version = annotation.name.prompt_version
        db.flush()

        if annotation.degraded:
            write_llm_degraded_alert(
                db,
                reason=self.monitor.reason or "llm_service 降级",
                since=self.monitor.degraded_since,
                redis_client=redis_client,
                debounce_seconds=self.settings.alert_debounce_seconds,
            )

    def _record_judgement(
        self, db: Session, topic_id: uuid.UUID | None, result: JudgementResult, extra_input: dict[str, Any]
    ) -> LLMJudgement:
        judgement = LLMJudgement(
            topic_id=topic_id,
            task_type=result.task_type,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
            input_payload=extra_input,
            output_payload={"value": result.value} if result.value is not None else None,
            success=result.success,
            naming_method=result.naming_method,
            error=result.error,
            latency_ms=int(result.latency_s * 1000),
        )
        db.add(judgement)
        db.flush()
        return judgement

    # ------------------------------------------------------------------
    # 降级回填（T2.16）
    # ------------------------------------------------------------------
    def backfill_degraded_topics(self, db: Session, limit: int = 50) -> int:
        """LLM 恢复后对降级期议题（naming_method=ctfidf_fallback）回填重命名/分类/摘要。

        代表标题从 topic_articles 关联 articles 重建；返回成功回填的议题数。
        人工锁定字段（human_locked_fields）不被机器推翻。
        """
        from app.models.article import Article
        from app.models.topic import TopicArticle

        topics = db.scalars(
            select(Topic)
            .where(Topic.naming_method == NAMING_FALLBACK)
            .order_by(Topic.last_seen_at.desc())
            .limit(limit)
        ).all()
        backfilled = 0
        for topic in topics:
            titles = db.scalars(
                select(Article.title)
                .join(TopicArticle, TopicArticle.article_id == Article.id)
                .where(TopicArticle.topic_id == topic.id)
                .order_by(TopicArticle.assigned_at.desc())
                .limit(10)
            ).all()
            top_words = list(topic.keywords or [])
            annotation = self.annotate_topic(list(titles), top_words)
            if annotation.degraded:
                continue  # LLM 仍未恢复，保持兜底状态
            before = {"name_auto": topic.name_auto, "topic_category": topic.topic_category}
            self.persist_annotation(db, topic, annotation)
            topic.revision_log = list(topic.revision_log or []) + [{
                "field": "name_auto",
                "before": before,
                "after": {"name_auto": topic.name_auto, "topic_category": topic.topic_category},
                "trigger": "llm_recovered_backfill",
                "actor": "machine",
                "model": topic.llm_model,
                "prompt_version": topic.prompt_version,
                "at": datetime.now(UTC).isoformat(),
            }]
            db.flush()
            backfilled += 1
        return backfilled

    # ------------------------------------------------------------------
    # 历史判定批量重跑对比（T2.17）
    # ------------------------------------------------------------------
    def rerun_judgements(
        self,
        db: Session,
        task_type: str,
        prompt_version: str,
        limit: int = 50,
        persist: bool = True,
    ) -> list[dict[str, Any]]:
        """用指定 prompt 版本重跑历史成功判定，返回前后对比列表。

        persist=True 时重跑结果作为新 judgement 留痕（input_payload 标记 rerun_of），
        不改动 topics 表现行值——是否采纳由分析师/后续流程决定。
        """
        baseline = db.scalars(
            select(LLMJudgement)
            .where(LLMJudgement.task_type == task_type, LLMJudgement.success.is_(True))
            .order_by(LLMJudgement.created_at.desc())
            .limit(limit)
        ).all()
        comparisons: list[dict[str, Any]] = []
        for old in baseline:
            payload = dict(old.input_payload or {})
            titles = list(payload.get("titles", []))
            top_words = list(payload.get("top_words", []))
            if not titles:
                continue
            rerun = self._run_task(
                task_type, titles, top_words, name=payload.get("name"), prompt_version=prompt_version
            )
            old_value = (old.output_payload or {}).get("value")
            comparisons.append({
                "judgement_id": str(old.id),
                "topic_id": str(old.topic_id) if old.topic_id else None,
                "old_version": old.prompt_version,
                "old_value": old_value,
                "new_version": rerun.prompt_version,
                "new_value": rerun.value,
                "changed": old_value != rerun.value,
            })
            if persist:
                self._record_judgement(db, old.topic_id, rerun, {
                    **payload, "rerun_of": str(old.id),
                })
        return comparisons
