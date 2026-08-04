"""监控对象关系抽取服务（每日跑批）。

流程：
  1. 取过去 N 小时的文章（is_duplicate=False，content_status='full'）
  2. 对每篇文章，检测含哪些种子实体（name + name_aliases 字符串匹配）
  3. 含 ≥1 种子实体的文章 → 调用 LLM 抽关系（relation-extract-v1 prompt）
  4. LLM 返回的每条 relation：
     - 校验 evidence_quote 是否为 article.content 子串（防幻觉）
     - 两端都是种子 → 直接 upsert
     - 一端种子 + 一端新实体 + confidence=high → 登记外围实体 + upsert
     - 其他 → 丢弃
  5. upsert entity_relations + 插 relation_evidences + 写 llm_judgements 留痕
  6. 全部完成后做时间衰减：confidence = base × exp(-days/τ)；低于阈值置 expired
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import prompts
from app.llm.errors import LLMError
from app.llm.schemas import RelationExtractOutput, RelationItem
from app.models.article import Article
from app.models.entity_relation import EntityRelation, RelationEvidence
from app.models.llm import LLMJudgement
from app.models.person import PersonOrg

logger = logging.getLogger(__name__)

# 衰减常数（τ=14 天）
DECAY_TAU_DAYS = 14.0
# 置信度低于此值标记 expired
EXPIRE_THRESHOLD = 0.2
# 单篇正文送给 LLM 的最大长度
MAX_CONTENT_LEN = 3000
# 单篇最多落库的关系条数
MAX_RELATIONS_PER_ARTICLE = 5


# ---------------------------------------------------------------------------
# 置信度
# ---------------------------------------------------------------------------
_CONFIDENCE_SCORE: dict[str, float] = {"high": 0.9, "medium": 0.7, "low": 0.4}


def confidence_score(level: str) -> float:
    return _CONFIDENCE_SCORE.get(level, 0.5)


def decay_confidence(base: float, last_seen_at: datetime, now: datetime) -> float:
    """指数衰减：base × exp(-days_since_last_seen/τ)。"""
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=UTC)
    days = (now - last_seen_at).total_seconds() / 86400.0
    return base * math.exp(-max(days, 0.0) / DECAY_TAU_DAYS)


# ---------------------------------------------------------------------------
# 主服务
# ---------------------------------------------------------------------------
class RelationExtractor:
    """对一批文章跑关系抽取并落库。"""

    def __init__(self, db: Session, annotator: Any):
        """annotator: TopicAnnotator 实例（有 engine.generate_structured 与 monitor）。"""
        self.db = db
        self.annotator = annotator
        self._seed_by_name: dict[str, PersonOrg] = {}
        self._seeds: list[PersonOrg] = []
        self._load_seeds()

    def _load_seeds(self) -> None:
        self._seeds = list(self.db.scalars(select(PersonOrg).where(PersonOrg.is_seed.is_(True))).all())
        for s in self._seeds:
            self._seed_by_name[s.name.lower()] = s
            for alias in s.name_aliases or []:
                self._seed_by_name[str(alias).lower()] = s
        logger.info("relation_extractor_seeds_loaded", extra={"count": len(self._seeds)})

    # ------------------------------------------------------------------
    # 文章筛选
    # ------------------------------------------------------------------
    def fetch_recent_articles(self, hours: int = 24, limit: int = 500) -> list[Article]:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        stmt = (
            select(Article)
            .where(
                Article.published_at >= cutoff,
                Article.is_duplicate.is_(False),
                Article.content_status == "full",
                Article.content.isnot(None),
            )
            .order_by(Article.published_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    # ------------------------------------------------------------------
    # 单篇命中哪些种子
    # ------------------------------------------------------------------
    def hit_seeds(self, article: Article) -> list[PersonOrg]:
        text = f"{article.title}\n{article.content or ''}".lower()
        hits: dict[uuid.UUID, PersonOrg] = {}
        for s in self._seeds:
            names = [s.name] + list(s.name_aliases or [])
            if s.name_zh:
                names.append(s.name_zh)
            for n in names:
                if n and str(n).lower() in text:
                    hits[s.id] = s
                    break
        return list(hits.values())

    # ------------------------------------------------------------------
    # LLM 调用 + 留痕
    # ------------------------------------------------------------------
    def extract_for_article(
        self, article: Article, seeds_in_article: list[PersonOrg]
    ) -> tuple[RelationExtractOutput | None, uuid.UUID | None]:
        """调用 LLM；返回 (解析结果, llm_judgement_id)。失败返回 (None, judgement_id)。"""
        if not seeds_in_article:
            return None, None
        engine = self.annotator.engine
        if not engine.is_loaded:
            engine.load()
        template = prompts.get_prompt(prompts.TASK_RELATION_EXTRACT)
        payload = {
            "seed_entities": [
                {
                    "name": s.name,
                    "name_zh": s.name_zh,
                    "role_title": s.role_title,
                    "name_aliases": s.name_aliases,
                }
                for s in seeds_in_article
            ],
            "article_title": article.title,
            "article_content": (article.content or "")[:MAX_CONTENT_LEN],
        }
        user_prompt = template.build_user(payload)
        started = time.monotonic()
        judgement = LLMJudgement(
            topic_id=None,
            task_type=prompts.TASK_RELATION_EXTRACT,
            model_name=getattr(engine, "model_name", "unknown"),
            prompt_version=template.version,
            input_payload={
                "article_id": str(article.id),
                "article_title": article.title[:200],
                "seed_count": len(seeds_in_article),
            },
            success=False,
        )
        try:
            parsed, latency = engine.generate_structured(template.system, user_prompt, RelationExtractOutput)
            judgement.success = True
            judgement.output_payload = parsed.model_dump()
            judgement.latency_ms = int(latency * 1000)
            self.db.add(judgement)
            self.db.flush()
            return parsed, judgement.id
        except LLMError as exc:
            judgement.error = str(exc)[:500]
            judgement.latency_ms = int((time.monotonic() - started) * 1000)
            self.db.add(judgement)
            self.db.flush()
            logger.warning("relation_extract_llm_fail", extra={"error": str(exc)[:200]})
            return None, judgement.id
        except Exception as exc:  # noqa: BLE001
            judgement.error = f"unexpected: {exc}"[:500]
            judgement.latency_ms = int((time.monotonic() - started) * 1000)
            self.db.add(judgement)
            self.db.flush()
            logger.exception("relation_extract_unexpected")
            return None, judgement.id

    # ------------------------------------------------------------------
    # 关系落库
    # ------------------------------------------------------------------
    def _find_or_register_entity(
        self,
        name: str,
        is_new: bool,
        new_type: str | None,
        new_role: str | None,
        confidence: str,
        country_code_fallback: str = "US",
    ) -> PersonOrg | None:
        """按 name 在种子/全表查找；找不到且 is_new=True 且 confidence=high 时登记外围实体。"""
        key = name.strip().lower()
        # 先查种子别名表
        hit = self._seed_by_name.get(key)
        if hit is not None:
            return hit
        # 全表模糊匹配 name 或 name_aliases
        stmt = select(PersonOrg).where(PersonOrg.name.ilike(name.strip()))
        entity = self.db.scalar(stmt)
        if entity is not None:
            return entity
        # 找不到：is_new=True + confidence=high 才登记外围
        if not is_new or confidence != "high":
            return None
        entity = PersonOrg(
            entity_type=(new_type if new_type in ("person", "thinktank", "intl_org", "gov_body") else "person"),
            name=name.strip(),
            name_zh=None,
            name_aliases=[],
            country_code=country_code_fallback,
            role_title=(new_role or "")[:200] or None,
            monitored=False,
            is_seed=False,
            category="外围",
            priority=0,
        )
        self.db.add(entity)
        self.db.flush()
        logger.info("relation_extractor_peripheral_registered", extra={"name": name})
        return entity

    def _upsert_relation(
        self,
        subject: PersonOrg,
        obj: PersonOrg,
        relation_type: str,
        confidence: str,
        evidence_at: datetime,
    ) -> EntityRelation:
        stmt = select(EntityRelation).where(
            EntityRelation.subject_entity_id == subject.id,
            EntityRelation.object_entity_id == obj.id,
            EntityRelation.relation_type == relation_type,
        )
        rel = self.db.scalar(stmt)
        base = confidence_score(confidence)
        if rel is None:
            rel = EntityRelation(
                subject_entity_id=subject.id,
                object_entity_id=obj.id,
                relation_type=relation_type,
                confidence=Decimal(str(base)),
                base_confidence=Decimal(str(base)),
                first_seen_at=evidence_at,
                last_seen_at=evidence_at,
                evidence_count=1,
                status="active",
            )
            self.db.add(rel)
            self.db.flush()
        else:
            rel.evidence_count += 1
            if evidence_at > rel.last_seen_at:
                rel.last_seen_at = evidence_at
            if evidence_at < rel.first_seen_at:
                rel.first_seen_at = evidence_at
            # base 取最大（新证据更强则替换）
            new_base = max(float(rel.base_confidence), base)
            rel.base_confidence = Decimal(str(new_base))
            rel.confidence = Decimal(str(new_base))  # 让衰减任务下次统一重算
            rel.status = "active"
            self.db.add(rel)
            self.db.flush()
        return rel

    def _insert_evidence(
        self,
        relation: EntityRelation,
        article: Article,
        item: RelationItem,
        judgement_id: uuid.UUID | None,
    ) -> bool:
        """唯一约束 (relation_id, article_id)；冲突则跳过。"""
        stmt = select(RelationEvidence).where(
            RelationEvidence.relation_id == relation.id,
            RelationEvidence.article_id == article.id,
        )
        if self.db.scalar(stmt) is not None:
            return False
        ev = RelationEvidence(
            relation_id=relation.id,
            article_id=article.id,
            evidence_quote=item.evidence_quote,
            context_paragraph=None,
            published_at=article.published_at,
            llm_judgement_id=judgement_id,
        )
        self.db.add(ev)
        return True

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def process_article(self, article: Article) -> dict[str, int]:
        """单篇文章处理；返回统计计数。"""
        stats = {"relations_new": 0, "relations_updated": 0, "evidences": 0, "dropped": 0}
        hits = self.hit_seeds(article)
        if not hits:
            return stats
        output, judgement_id = self.extract_for_article(article, hits)
        if output is None or not output.relations:
            return stats
        content = article.content or ""
        for item in output.relations[:MAX_RELATIONS_PER_ARTICLE]:
            # 1) 校验 evidence_quote 是原文子串
            if item.evidence_quote not in content:
                stats["dropped"] += 1
                continue
            # 2) 找/登记两端实体
            subject = self._find_or_register_entity(
                item.subject_name, item.subject_is_new,
                item.new_entity_type, item.new_entity_role, item.confidence,
                country_code_fallback=article.country_code,
            )
            obj = self._find_or_register_entity(
                item.object_name, item.object_is_new,
                item.new_entity_type, item.new_entity_role, item.confidence,
                country_code_fallback=article.country_code,
            )
            if subject is None or obj is None or subject.id == obj.id:
                stats["dropped"] += 1
                continue
            # 3) 至少一端必须是种子（防图谱膨胀）
            if not (subject.is_seed or obj.is_seed):
                stats["dropped"] += 1
                continue
            # 4) upsert 关系
            rel_before = self.db.scalar(
                select(EntityRelation).where(
                    EntityRelation.subject_entity_id == subject.id,
                    EntityRelation.object_entity_id == obj.id,
                    EntityRelation.relation_type == item.relation,
                )
            )
            relation = self._upsert_relation(subject, obj, item.relation, item.confidence, article.published_at)
            if rel_before is None:
                stats["relations_new"] += 1
            else:
                stats["relations_updated"] += 1
            # 5) 插证据
            if self._insert_evidence(relation, article, item, judgement_id):
                stats["evidences"] += 1
        return stats

    def apply_time_decay(self, now: datetime | None = None) -> int:
        """对所有 active 关系做时间衰减；低于阈值置 expired。返回处理数。"""
        now = now or datetime.now(UTC)
        stmt = select(EntityRelation).where(EntityRelation.status == "active")
        relations = list(self.db.scalars(stmt).all())
        expired_count = 0
        for rel in relations:
            base = float(rel.base_confidence)
            last = rel.last_seen_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            new_conf = decay_confidence(base, last, now)
            rel.confidence = Decimal(str(round(new_conf, 3)))
            if new_conf < EXPIRE_THRESHOLD:
                rel.status = "expired"
                expired_count += 1
            self.db.add(rel)
        self.db.flush()
        return expired_count


# ---------------------------------------------------------------------------
# 跑批入口
# ---------------------------------------------------------------------------
def run_relation_extraction_round(
    db: Session,
    annotator: Any,
    hours: int = 24,
    limit: int = 500,
) -> dict[str, int]:
    """每日跑批入口；返回总统计。"""
    extractor = RelationExtractor(db, annotator)
    articles = extractor.fetch_recent_articles(hours=hours, limit=limit)
    total = {"articles": len(articles), "relations_new": 0, "relations_updated": 0, "evidences": 0, "dropped": 0}
    for art in articles:
        try:
            stats = extractor.process_article(art)
            for k in ("relations_new", "relations_updated", "evidences", "dropped"):
                total[k] += stats[k]
        except Exception as exc:  # noqa: BLE001
            logger.exception("relation_extract_article_fail", extra={"article_id": str(art.id), "error": str(exc)[:200]})
            continue
    total["expired"] = extractor.apply_time_decay()
    db.commit()
    logger.info("relation_extraction_round_done", extra=total)
    return total
