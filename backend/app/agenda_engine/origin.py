"""媒体首发锚点判定（T3.6）与跟随国序列计算（T3.9，详细设计 4.2 算法 4）。

算法口径（详细设计 2384-2420 行）：
  media_origin ← argmin_{n in nodes} n.earliest_pub
  followers    ← group_by_country(nodes, exclude=origin.country)
                  .map(c → {country: c, first_media, lag_hours: first_pub(c) - origin.at})
                  .sort by lag_hours

判定规则（与算法 4 注释一致）：
  - 议题 topic_articles 关联 articles，过滤已 is_duplicate=True 的转载（保留原创节点）
  - 通讯社优先判定：候选来源是通讯社时，其报道时间锚点向前倾斜
    origin_wire_boost_hours 小时参与比较（通讯社原文通常早于转载与跟风稿，
    倾斜通讯社更早成为首发锚点）；普通媒体按真实 published_at 比较
  - 候选来源是通讯社（source.name 大小写不敏感命中 origin_wire_services 名单，
    或 source.media_type='wire'/'agency'），且 is_duplicate=False：confidence='high'
  - 普通媒体原创：confidence='medium'
  - time_source='crawled'：发布时间实际为抓取时间，置信度低，
    confidence='low' 且 needs_review=True，不自动告警（前端标注"首发源待核实"）
  - 同一秒并列最早（倾斜后）：通讯社优先（is_wire_service=True 胜出）

跟随国序列：
  - 排除 origin.country_code，对其他国家 c 取该国内媒体首篇该议题报道
  - lag_hours = (c 首篇 published_at - origin.published_at).total_seconds() / 3600
  - lag_hours < 0：判定有误，跳过且记 warning（早于首发的"跟随"在业务上无意义）
  - lag_hours > follower_window_days * 24：超窗剔除（默认 14 天窗口）
  - 按 lag_hours 升序返回（最早跟随者在前）

纯计算函数：不写库，由调用方（M3-3 事件判定环节）负责落 AgendaEvent / revision_log。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.config import get_agenda_settings
from app.models.article import Article
from app.models.source import Source
from app.models.topic import TopicArticle

logger = logging.getLogger("agenda_engine.origin")

Confidence = Literal["high", "medium", "low"]

# 媒体类型中视为"通讯社"的取值：sources.media_type 列受 CHECK 约束仅允许
# ('newspaper','agency','broadcast','online')；同时兼容历史/外部数据中出现的 'wire'
_WIRE_MEDIA_TYPES: frozenset[str] = frozenset({"agency", "wire"})


@dataclass(frozen=True)
class MediaOrigin:
    """媒体首发锚点：议题内最早 published_at 的原创报道（T3.6）。"""

    article_id: UUID
    source_id: UUID
    source_name: str
    country_code: str
    published_at: datetime  # UTC
    is_wire_service: bool
    confidence: Confidence
    needs_review: bool  # True 当 confidence='low'，前端提示"首发源待核实"


@dataclass(frozen=True)
class CountryFollower:
    """跟随国序列单条：某国媒体首篇该议题报道（T3.9）。"""

    country_code: str
    first_media_id: UUID
    first_media_name: str
    first_article_id: UUID
    first_published_at: datetime
    lag_hours: float


def _is_wire_service(source: Source, wire_names: list[str]) -> bool:
    """判定来源是否通讯社：
      - source.media_type ∈ {'agency', 'wire'}（直接判通讯社，无需走名单）
      - source.name 大小写不敏感匹配 origin_wire_services 名单（兼容外文别名）
    """
    media_type = (source.media_type or "").strip().lower()
    if media_type in _WIRE_MEDIA_TYPES:
        return True
    name = (source.name or "").strip().lower()
    if not name:
        return False
    return any(name == wire.strip().lower() for wire in wire_names if wire.strip())


def _classify_confidence(article: Article, is_wire: bool) -> tuple[Confidence, bool]:
    """按 time_source / is_wire 判定置信度与 needs_review。

    规则：
      - time_source='crawled'：发布时间为抓取时间，最不可信 → ('low', True)
      - 通讯社原创（is_wire=True 且非转载） → ('high', False)
      - 其他普通媒体原创 → ('medium', False)
    """
    if article.time_source == "crawled":
        return "low", True
    if is_wire:
        return "high", False
    return "medium", False


def _load_topic_articles(db: Session, topic_id: UUID) -> list[tuple[Article, Source]]:
    """读取议题下全部"已原创且未被回声折叠为转载"的文章，关联其来源。

    过滤口径：
      - 仅 is_duplicate=False：转载跟风稿已折叠，canonical 节点承担首发锚点角色
      - 按 published_at 升序返回，供首发锚点与跟随国序列共用
    """
    stmt = (
        select(Article, Source)
        .join(TopicArticle, TopicArticle.article_id == Article.id)
        .join(Source, Source.id == Article.source_id)
        .where(
            TopicArticle.topic_id == topic_id,
            Article.is_duplicate.is_(False),
        )
        .order_by(Article.published_at.asc(), Article.id.asc())
    )
    return [(row[0], row[1]) for row in db.execute(stmt).all()]


def detect_media_origin(db: Session, topic_id: UUID) -> MediaOrigin | None:
    """议题内最早 published_at 的原创报道为首发锚点。

    通讯社优先判定：通讯社候选的比较锚点 = published_at - origin_wire_boost_hours
    （通讯社原文优先于转载的倾斜策略）；返回的 MediaOrigin.published_at 仍为真实
    发布时间（倾斜只影响挑选，不改写留痕时间）。

    返回 None：议题下无任何可用原创报道（空议题或全部已被折叠为转载）。
    """
    settings = get_agenda_settings()
    candidates = _load_topic_articles(db, topic_id)
    if not candidates:
        logger.info("media_origin_no_articles", extra={"topic_id": str(topic_id)})
        return None

    boost = timedelta(hours=settings.origin_wire_boost_hours)

    def _effective_anchor(article: Article, is_wire: bool) -> datetime:
        """通讯社候选锚点向前倾斜 boost 小时参与比较；普通媒体按真实时间。"""
        if is_wire:
            return article.published_at - boost
        return article.published_at

    # 按（倾斜后锚点, 非通讯社, 真实时间, id）排序取最优：倾斜后并列时通讯社优先
    scored = [
        (_effective_anchor(a, _is_wire_service(s, settings.origin_wire_services)), a, s)
        for a, s in candidates
    ]
    earliest_effective = min(item[0] for item in scored)
    tied = [item for item in scored if item[0] == earliest_effective]
    chosen_article: Article | None = None
    chosen_source: Source | None = None
    chosen_is_wire = False
    for _anchor, article, source in tied:
        is_wire = _is_wire_service(source, settings.origin_wire_services)
        if chosen_article is None:
            chosen_article, chosen_source, chosen_is_wire = article, source, is_wire
            continue
        if is_wire and not chosen_is_wire:
            chosen_article, chosen_source, chosen_is_wire = article, source, is_wire

    assert chosen_article is not None and chosen_source is not None  # 上文已确保 tied 非空
    confidence, needs_review = _classify_confidence(chosen_article, chosen_is_wire)
    # boost 是否改变了结果（相对纯按真实 published_at 的最早者）
    earliest_real = candidates[0][0]
    boost_changed_outcome = chosen_article.id != earliest_real.id
    origin = MediaOrigin(
        article_id=chosen_article.id,
        source_id=chosen_source.id,
        source_name=chosen_source.name,
        country_code=chosen_article.country_code,
        published_at=chosen_article.published_at,
        is_wire_service=chosen_is_wire,
        confidence=confidence,
        needs_review=needs_review,
    )
    logger.info(
        "media_origin_detected",
        extra={
            "topic_id": str(topic_id),
            "article_id": str(origin.article_id),
            "source_id": str(origin.source_id),
            "country_code": origin.country_code,
            "is_wire_service": origin.is_wire_service,
            "confidence": origin.confidence,
            "needs_review": origin.needs_review,
            "wire_boost_hours": settings.origin_wire_boost_hours,
            "wire_boost_changed_outcome": boost_changed_outcome,
        },
    )
    return origin


def compute_follower_sequence(
    db: Session,
    topic_id: UUID,
    origin: MediaOrigin,
    window_days: int | None = None,
) -> list[CountryFollower]:
    """计算各国媒体首篇该议题报道相对 origin 的时滞序列（T3.9）。

    口径：
      - 排除 origin.country_code
      - 每个国家 c 取该国 earliest published_at 的原创报道（is_duplicate=False）
      - lag_hours = (c 首篇 published_at - origin.published_at).total_seconds() / 3600
      - lag_hours < 0：跳过且记 warning（早于首发的"跟随"在业务上无意义，提示判定可能有误）
      - lag_hours > window_days * 24：超窗剔除
      - 按 lag_hours 升序返回
    """
    settings = get_agenda_settings()
    days = window_days if window_days is not None else settings.follower_window_days
    max_lag_hours = float(days) * 24.0

    candidates = _load_topic_articles(db, topic_id)
    # 按国家分组保留最早一篇（candidates 已按 published_at 升序，先到先得）
    by_country: dict[str, tuple[Article, Source]] = {}
    for article, source in candidates:
        if article.country_code == origin.country_code:
            continue
        if article.country_code in by_country:
            continue
        by_country[article.country_code] = (article, source)

    followers: list[CountryFollower] = []
    for country_code, (article, source) in by_country.items():
        lag_seconds = (article.published_at - origin.published_at).total_seconds()
        lag_hours = lag_seconds / 3600.0
        if lag_hours < 0:
            logger.warning(
                "follower_lag_negative_skipped",
                extra={
                    "topic_id": str(topic_id),
                    "country_code": country_code,
                    "article_id": str(article.id),
                    "origin_article_id": str(origin.article_id),
                    "lag_hours": round(lag_hours, 4),
                },
            )
            continue
        if lag_hours > max_lag_hours:
            logger.info(
                "follower_lag_out_of_window",
                extra={
                    "topic_id": str(topic_id),
                    "country_code": country_code,
                    "article_id": str(article.id),
                    "lag_hours": round(lag_hours, 4),
                    "window_days": days,
                },
            )
            continue
        followers.append(
            CountryFollower(
                country_code=country_code,
                first_media_id=source.id,
                first_media_name=source.name,
                first_article_id=article.id,
                first_published_at=article.published_at,
                lag_hours=lag_hours,
            )
        )
    followers.sort(key=lambda f: (f.lag_hours, f.country_code))
    logger.info(
        "follower_sequence_computed",
        extra={
            "topic_id": str(topic_id),
            "origin_article_id": str(origin.article_id),
            "origin_country": origin.country_code,
            "follower_count": len(followers),
            "window_days": days,
        },
    )
    return followers
