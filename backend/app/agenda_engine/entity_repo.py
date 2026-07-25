"""persons_orgs 实体库 CRUD 与 NER 提及识别（T3.7，详细设计 2.12 / 4.2 算法 4）。

职责：
- 实体登记：find_or_create_entity 按 (name, entity_type, country_code) 查重，
  新实体自动并入 name_zh 到 name_aliases，monitored=True；同名歧义不合并、不更新；
- 提及识别：match_entities_in_text 从文本识别已登记实体的提及（子串匹配 + 同名
  歧义按上下文 country_code 衰减 + 黑名单命中降权），输出 EntityMention 供 LLM
  首发判定器与人工复核队列消费；
- 首发表述档案：update_first_utterances 追加 occurred_at 升序的 JSONB 记录，供
  LLM 判定器取"实体历史表述摘要"做对比（详细设计 4.2 算法 4 llm_first_utterance）。

与 T3.5 黑名单联动：命中 entity:blacklist 的实体 confidence × 0.3，防超级节点
虚假关联（IIS 经验）；与 T3.8 LLM 判定器联动：needs_review=True 的提及不进首发
判定，先经人工确认实体身份。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.config import AgendaSettings, get_agenda_settings
from app.agenda_engine.entity_blacklist import is_blacklisted
from app.models.person import PersonOrg

if TYPE_CHECKING:
    import redis

logger = structlog.get_logger(__name__)

# 国家代码 → 上下文关键词（大小写不敏感匹配文本中的国家指代）
# 用于同名歧义衰减：文本提到 "US"/"美国" 则美国实体置信度不衰减，他国衰减
_COUNTRY_HINTS: dict[str, tuple[str, ...]] = {
    "US": ("US", "USA", "U.S.", "United States", "America", "American", "美国", "美方", "华盛顿"),
    "CN": ("CN", "China", "Chinese", "Beijing", "中国", "中方", "北京"),
    "RU": ("RU", "Russia", "Russian", "Moscow", "俄罗斯", "俄方", "莫斯科"),
    "GB": ("GB", "UK", "Britain", "British", "London", "英国", "英方", "伦敦"),
    "JP": ("JP", "Japan", "Japanese", "Tokyo", "日本", "日方", "东京"),
    "KR": ("KR", "Korea", "Korean", "Seoul", "韩国", "韩方", "首尔"),
    "DE": ("DE", "Germany", "German", "Berlin", "德国", "德方", "柏林"),
    "FR": ("FR", "France", "French", "Paris", "法国", "法方", "巴黎"),
    "IN": ("IN", "India", "Indian", "New Delhi", "印度", "印方", "新德里"),
    "UA": ("UA", "Ukraine", "Ukrainian", "Kyiv", "乌克兰", "乌方", "基辅"),
    "IL": ("IL", "Israel", "Israeli", "Jerusalem", "以色列", "以方", "耶路撒冷"),
    "IR": ("IR", "Iran", "Iranian", "Tehran", "伊朗", "伊方", "德黑兰"),
    "KP": ("KP", "North Korea", "Pyongyang", "朝鲜", "平壤"),
    "TW": ("TW", "Taiwan", "Taiwanese", "台湾", "台北"),
}

# 英文 alias 提取用：拉丁词字符（含所有格与连字符），与 entity_extract 一致
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'.-]*")
# 中文 alias 判定（CJK 统一表意文字）
_CJK_RE = re.compile(r"[一-鿿]")


@dataclass(frozen=True)
class EntityMention:
    """一次实体提及识别结果（不持久化，由调用方决定是否落 first_utterances/人工队列）。"""

    entity_id: uuid.UUID
    entity_name: str
    entity_type: str  # person/thinktank/intl_org/gov_body
    country_code: str
    confidence: float  # 0-1；别名精确匹配 1.0，歧义/黑名单衰减后可低于阈值
    matched_alias: str  # 实际命中的别名（name/name_zh/name_aliases 之一）
    needs_review: bool  # True 当 confidence < entity_ambiguity_low_confidence（进人工队列）


def _normalize_alias(alias: str) -> str:
    """别名规范化：去首尾空白；英文别名统一小写（大小写不敏感匹配用），中文保持原样。"""
    alias = alias.strip()
    if not alias:
        return ""
    if _CJK_RE.search(alias):
        return alias
    return alias.lower()


def _collect_aliases(entity: PersonOrg) -> list[str]:
    """汇总实体的所有候选别名（name + name_zh + name_aliases），去重、去空。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in [entity.name, entity.name_zh, *(entity.name_aliases or [])]:
        if not raw:
            continue
        normalized = _normalize_alias(str(raw))
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def _alias_in_text(alias: str, text: str) -> bool:
    """判断别名是否在文本中作为子串出现。
    中文别名直接子串匹配；英文别名在 lowercase 文本中做整词边界匹配（避免 'US' 命中 'USer'）。
    """
    if not alias or not text:
        return False
    if _CJK_RE.search(alias):
        return alias in text
    lowered = text.lower()
    # 英文别名按非字母数字边界匹配：(?<![A-Za-z0-9])alias(?![A-Za-z0-9])
    pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])")
    return bool(pattern.search(lowered))


def _detect_countries_in_text(text: str) -> set[str]:
    """扫描文本中出现的国家指代，返回命中的 country_code 集合（用于同名歧义衰减）。"""
    if not text:
        return set()
    lowered = text.lower()
    found: set[str] = set()
    for code, hints in _COUNTRY_HINTS.items():
        for hint in hints:
            if not hint:
                continue
            if _CJK_RE.search(hint):
                if hint in text:
                    found.add(code)
                    break
            else:
                pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(hint.lower()) + r"(?![A-Za-z0-9])")
                if pattern.search(lowered):
                    found.add(code)
                    break
    return found


def find_or_create_entity(
    db: Session,
    name: str,
    entity_type: str,
    country_code: str,
    name_zh: str | None = None,
    role_title: str | None = None,
) -> PersonOrg:
    """按 (name, entity_type, country_code) 查重，无则新建（monitored=True）。

    - name_aliases 自动并入 name_zh（若提供且与 name 不同），保持别名可匹配中/英；
    - 查重命中已有实体：直接返回，不修改（避免误合并同名歧义实体；调用方负责判 confidence）；
    - entity_type 必须在 ('person','thinktank','intl_org','gov_body') 内（DB CheckConstraint）。
    """
    name_clean = name.strip()
    if not name_clean:
        raise ValueError("实体名不能为空")
    if entity_type not in ("person", "thinktank", "intl_org", "gov_body"):
        raise ValueError(f"非法 entity_type: {entity_type}")
    if len(country_code) != 2:
        raise ValueError(f"country_code 必须为 ISO 3166-1 alpha-2 两位码: {country_code!r}")

    stmt = select(PersonOrg).where(
        PersonOrg.name == name_clean,
        PersonOrg.entity_type == entity_type,
        PersonOrg.country_code == country_code,
    )
    existing = db.scalars(stmt).first()
    if existing is not None:
        return existing

    aliases: list[str] = []
    if name_zh and name_zh.strip() and name_zh.strip() != name_clean:
        aliases.append(name_zh.strip())

    entity = PersonOrg(
        entity_type=entity_type,
        name=name_clean,
        name_zh=name_zh.strip() if name_zh else None,
        name_aliases=aliases,
        country_code=country_code,
        role_title=role_title.strip() if role_title else None,
        monitored=True,
        first_utterances=[],
    )
    db.add(entity)
    db.flush()
    logger.info(
        "persons_orgs_entity_created",
        entity_id=str(entity.id),
        name=name_clean,
        entity_type=entity_type,
        country_code=country_code,
    )
    return entity


def match_entities_in_text(
    db: Session,
    text: str,
    *,
    min_confidence: float | None = None,
    redis_client: redis.Redis | None = None,
    settings: AgendaSettings | None = None,
) -> list[EntityMention]:
    """从文本识别已登记实体的提及（详细设计 4.2 算法 4 前置：实体提及 → LLM 首发判定）。

    规则：
    - 加载所有 monitored=True 实体的 name + name_zh + name_aliases；
    - 文本中出现精确子串（英文按整词边界，中文直接子串）即候选；
    - 同名歧义（多实体共享同一别名）：按上下文 country_code 出现频率衰减置信度——
      实体 country_code 在文本国家指代集合中则 ×1.0，否则 ×0.5；
    - 命中实体黑名单（entity_blacklist.is_blacklisted）再 ×0.3（防超级节点虚假关联）；
    - confidence < entity_ambiguity_low_confidence 标记 needs_review=True（进人工队列）；
    - min_confidence 提供时过滤低置信结果（不过滤 needs_review 标记本身）。
    """
    settings = settings or get_agenda_settings()
    if not text:
        return []

    entities = db.scalars(select(PersonOrg).where(PersonOrg.monitored.is_(True))).all()
    if not entities:
        return []

    # 别名 → 候选实体列表（同名歧义检测的载体）
    alias_to_entities: dict[str, list[PersonOrg]] = {}
    for entity in entities:
        for alias in _collect_aliases(entity):
            alias_to_entities.setdefault(alias, []).append(entity)

    countries_in_text = _detect_countries_in_text(text)

    mentions: list[EntityMention] = []
    seen_pair: set[tuple[uuid.UUID, str]] = set()
    for alias, candidates in alias_to_entities.items():
        if not _alias_in_text(alias, text):
            continue
        is_ambiguous = len(candidates) > 1
        for entity in candidates:
            pair = (entity.id, alias)
            if pair in seen_pair:
                continue
            seen_pair.add(pair)

            confidence = 1.0
            if is_ambiguous:
                # 同名歧义：上下文含该实体国家指代 → 不衰减；否则按 mismatch_dampen 衰减
                if entity.country_code in countries_in_text:
                    confidence *= settings.entity_country_match_boost
                else:
                    confidence *= settings.entity_country_mismatch_dampen
            # 黑名单命中降权（Redis 未注入视为不命中：实体库在缺 Redis 时不应被黑名单拦截）
            if redis_client is not None and is_blacklisted(alias, redis_client):
                confidence *= settings.entity_blacklist_dampen

            needs_review = confidence < settings.entity_ambiguity_low_confidence
            if min_confidence is not None and confidence < min_confidence:
                continue

            mentions.append(
                EntityMention(
                    entity_id=entity.id,
                    entity_name=entity.name,
                    entity_type=entity.entity_type,
                    country_code=entity.country_code,
                    confidence=confidence,
                    matched_alias=alias,
                    needs_review=needs_review,
                )
            )
    # 按 confidence 降序、实体名兜底排序，输出确定性
    mentions.sort(key=lambda m: (-m.confidence, m.entity_name, m.matched_alias))
    return mentions


def update_first_utterances(
    db: Session,
    entity_id: uuid.UUID,
    *,
    article_id: uuid.UUID,
    quote: str,
    occurred_at: datetime,
    detection_method: str,
    model: str,
    prompt_version: str,
) -> PersonOrg:
    """追加首发表述记录（first_utterances JSONB 数组），保持 occurred_at 升序。

    每条记录字段（详细设计 2.12 persons_orgs.first_utterances）：
      article_id / quote / occurred_at / detection_method / model / prompt_version / created_at
    重复 article_id 不重复追加（幂等：worker 重试/重复触发安全）。
    """
    entity = db.get(PersonOrg, entity_id)
    if entity is None:
        raise KeyError(f"实体不存在: {entity_id}")
    quote_clean = quote.strip()
    if not quote_clean:
        raise ValueError("evidence quote 不能为空（无依据表述不允许入库）")

    # occurred_at 统一为 UTC ISO 字符串（JSONB 序列化稳定）
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    occurred_iso = occurred_at.astimezone(UTC).isoformat()

    history: list[dict[str, Any]] = list(entity.first_utterances or [])
    # 幂等：同 article_id 已存在则不重复追加（quote 取先写为准）
    for record in history:
        if record.get("article_id") == str(article_id):
            return entity

    history.append(
        {
            "article_id": str(article_id),
            "quote": quote_clean,
            "occurred_at": occurred_iso,
            "detection_method": detection_method,
            "model": model,
            "prompt_version": prompt_version,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    # 保持 occurred_at 升序（详细设计：供 LLM 判定器取"实体历史表述摘要"）
    history.sort(key=lambda r: r["occurred_at"])
    # JSONB 字段需要重新赋值触发变更检测（SQLAlchemy 不会深度跟踪 list 内部变化）
    entity.first_utterances = history
    db.flush()
    logger.info(
        "persons_orgs_first_utterance_appended",
        entity_id=str(entity_id),
        article_id=str(article_id),
        detection_method=detection_method,
        history_size=len(history),
    )
    return entity


def get_recent_first_utterances(
    entity: PersonOrg, limit: int = 5
) -> list[dict[str, Any]]:
    """取实体近 N 条首发表述（保持 occurred_at 升序，喂给 LLM 判定器做对比）。

    详细设计 4.2 算法 4：候选全文片段 + 实体历史表述摘要 ≤4000 token。
    """
    history = list(entity.first_utterances or [])
    if limit <= 0:
        return []
    return history[-limit:] if len(history) > limit else history
