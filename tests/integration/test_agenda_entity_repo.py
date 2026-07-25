"""T3.7 persons_orgs 实体库集成测试：真实 PostgreSQL + Redis 链路。

验证 entity_repo 在真实数据库与 Redis 黑名单下的端到端行为：
- find_or_create_entity 落库可查；
- match_entities_in_text 真实 Redis 黑名单降权；
- update_first_utterances JSONB 持久化与读回。

模型无关，无需跳过；但依赖 docker postgres 与 redis（conftest 自动 skip）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.entity_repo import (
    find_or_create_entity,
    match_entities_in_text,
    update_first_utterances,
)
from app.models.person import PersonOrg

pytestmark = pytest.mark.integration

T0 = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


def test_entity_repo_end_to_end(db):
    """实体库 CRUD → NER 提及识别 → 首发表述档案：完整链路真实落库读回。"""
    entity = find_or_create_entity(
        db, name="Joe Biden", entity_type="person", country_code="US",
        name_zh="拜登", role_title="President",
    )
    db.commit()

    # NER 提及识别（无黑名单干扰）
    mentions = match_entities_in_text(db, "拜登今日在白宫发表讲话，Joe Biden also spoke.")
    assert len(mentions) >= 1
    biden = next(m for m in mentions if m.entity_name == "Joe Biden")
    assert biden.confidence == pytest.approx(1.0)
    assert biden.needs_review is False

    # 追加 3 条首发表述（乱序 occurred_at）
    art_ids = [uuid.uuid4() for _ in range(3)]
    update_first_utterances(
        db, entity.id, article_id=art_ids[1], quote="q2",
        occurred_at=T0 + timedelta(days=2),
        detection_method="llm_first_utterance", model="m", prompt_version="v1",
    )
    update_first_utterances(
        db, entity.id, article_id=art_ids[0], quote="q1", occurred_at=T0,
        detection_method="llm_first_utterance", model="m", prompt_version="v1",
    )
    update_first_utterances(
        db, entity.id, article_id=art_ids[2], quote="q3",
        occurred_at=T0 + timedelta(days=5),
        detection_method="llm_first_utterance", model="m", prompt_version="v1",
    )
    db.commit()

    # 读回：升序保持
    db.expire_all()
    refreshed = db.get(PersonOrg, entity.id)
    history = refreshed.first_utterances
    assert len(history) == 3
    occurred = [r["occurred_at"] for r in history]
    assert occurred == sorted(occurred)
    assert history[0]["article_id"] == str(art_ids[0])


def test_entity_repo_blacklist_dampens_via_real_redis(db, redis_client):
    """真实 Redis 黑名单：alias 入黑名单后 match_entities_in_text 降权 ×0.3。"""
    settings = get_agenda_settings()
    find_or_create_entity(db, name="Washington Post", entity_type="gov_body", country_code="US")
    db.commit()

    # 真实写入 Redis Set（不 Mock）
    redis_client.sadd(settings.entity_blacklist_key, "washington post")
    try:
        mentions = match_entities_in_text(
            db,
            "The Washington Post published an editorial.",
            redis_client=redis_client,
        )
        assert len(mentions) == 1
        assert mentions[0].confidence == pytest.approx(0.3)
        assert mentions[0].needs_review is True
    finally:
        redis_client.srem(settings.entity_blacklist_key, "washington post")


def test_entity_repo_unmonitored_excluded(db):
    """monitored=False 的实体不参与 NER 提及识别（防冷宫实体污染识别结果）。"""
    entity = find_or_create_entity(db, name="Cold Entity", entity_type="thinktank", country_code="US")
    entity.monitored = False
    db.commit()

    mentions = match_entities_in_text(db, "Cold Entity published a report.")
    assert all(m.entity_name != "Cold Entity" for m in mentions)
