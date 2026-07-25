"""T3.7 persons_orgs 实体库与 NER 提及识别单元测试（真实 DB 会话，禁 Mock）。

场景：
1. find_or_create_entity 新建 + 查重 + 别名合并
2. match_entities_in_text 精确匹配单实体（中英双语）
3. 同名歧义衰减 confidence + needs_review 标记
4. 黑名单命中降权（真实 Redis，不可达时跳过）
5. update_first_utterances 按 occurred_at 升序保持有序 + 幂等去重
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.agenda_engine.entity_repo import (
    find_or_create_entity,
    match_entities_in_text,
    update_first_utterances,
)
from app.models.person import PersonOrg

T0 = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


# ---------- find_or_create_entity ----------


def test_find_or_create_entity_creates_new_with_alias_merge(db):
    """新建实体：name_zh 与 name 不同 → 自动并入 name_aliases。"""
    entity = find_or_create_entity(
        db, name="Joe Biden", entity_type="person", country_code="US",
        name_zh="拜登", role_title="President",
    )
    db.commit()

    assert entity.id is not None
    assert entity.name == "Joe Biden"
    assert entity.name_zh == "拜登"
    assert "拜登" in entity.name_aliases
    assert entity.monitored is True
    assert entity.country_code == "US"
    assert entity.entity_type == "person"
    assert entity.first_utterances == []


def test_find_or_create_entity_idempotent_returns_existing(db):
    """查重：同 (name, entity_type, country_code) 再次调用直接返回已有实体，不重复建行。"""
    first = find_or_create_entity(
        db, name="Vladimir Putin", entity_type="person", country_code="RU", name_zh="普京",
    )
    db.commit()
    second = find_or_create_entity(
        db, name="Vladimir Putin", entity_type="person", country_code="RU", name_zh="普京",
    )
    db.commit()
    assert first.id == second.id

    # 全表只有一行
    rows = db.query(PersonOrg).filter_by(name="Vladimir Putin").all()
    assert len(rows) == 1


def test_find_or_create_entity_same_name_different_country_creates_two(db):
    """同名不同国家 → 不合并（同名歧义保护），分别建行。"""
    us = find_or_create_entity(db, name="America First Committee", entity_type="thinktank", country_code="US")
    gb = find_or_create_entity(db, name="America First Committee", entity_type="thinktank", country_code="GB")
    db.commit()
    assert us.id != gb.id


def test_find_or_create_entity_validates_inputs(db):
    """空 name / 非法 entity_type / 非法 country_code 直接报错。"""
    with pytest.raises(ValueError):
        find_or_create_entity(db, name="  ", entity_type="person", country_code="US")
    with pytest.raises(ValueError):
        find_or_create_entity(db, name="X", entity_type="alien", country_code="US")
    with pytest.raises(ValueError):
        find_or_create_entity(db, name="X", entity_type="person", country_code="USA")


# ---------- match_entities_in_text ----------


def test_match_entities_in_text_exact_match_english(db):
    """英文实体整词边界精确匹配：'Biden' 命中 'Joe Biden' 实体（别名 joe biden）。"""
    find_or_create_entity(
        db, name="Joe Biden", entity_type="person", country_code="US", name_zh="拜登",
    )
    db.commit()

    mentions = match_entities_in_text(db, "Joe Biden spoke at the White House today.")
    assert len(mentions) >= 1
    biden = next(m for m in mentions if m.entity_name == "Joe Biden")
    assert biden.confidence == pytest.approx(1.0)
    assert biden.needs_review is False
    assert biden.matched_alias in ("joe biden", "拜登")


def test_match_entities_in_text_exact_match_chinese(db):
    """中文别名直接子串匹配：'拜登' 命中实体。"""
    find_or_create_entity(
        db, name="Joe Biden", entity_type="person", country_code="US", name_zh="拜登",
    )
    db.commit()
    mentions = match_entities_in_text(db, "拜登今日在白宫发表讲话，涉及中美关系。")
    assert len(mentions) >= 1
    biden = next(m for m in mentions if m.entity_name == "Joe Biden")
    assert biden.confidence == pytest.approx(1.0)
    assert biden.needs_review is False


def test_match_entities_in_text_english_word_boundary(db):
    """英文别名整词边界：'US' 不应命中 'User'/'BUS' 等子串（防误匹配）。"""
    find_or_create_entity(
        db, name="US", entity_type="gov_body", country_code="US", name_zh="美国政府",
    )
    db.commit()
    # "User" 中的 "us" 不应触发（前后是字母）；但独立 "US" 应触发
    mentions_no_match = match_entities_in_text(db, "The User clicked the button.")
    assert all(m.matched_alias != "us" for m in mentions_no_match)


def test_match_entities_in_text_ambiguity_country_boost_and_dampen(db):
    """同名歧义：上下文国家指代一致 ×1.0，不一致 ×0.5（低于 0.6 阈值进人工队列）。"""
    find_or_create_entity(db, name="Patriots Union", entity_type="thinktank", country_code="US")
    find_or_create_entity(db, name="Patriots Union", entity_type="thinktank", country_code="GB")
    db.commit()

    # 上下文提到 "Britain" → GB 实体置信度 1.0，US 实体置信度 0.5（< 0.6 → needs_review=True）
    text = "The Patriots Union released a report in London yesterday. Britain officials commented."
    mentions = match_entities_in_text(db, text)
    by_country = {m.country_code: m for m in mentions}
    assert by_country["GB"].confidence == pytest.approx(1.0)
    assert by_country["GB"].needs_review is False
    assert by_country["US"].confidence == pytest.approx(0.5)
    assert by_country["US"].needs_review is True  # 进人工复核队列


def test_match_entities_in_text_min_confidence_filter(db):
    """min_confidence 过滤：低于阈值的 mention 不返回（needs_review 标记本身不被过滤）。"""
    find_or_create_entity(db, name="Patriots Union", entity_type="thinktank", country_code="US")
    find_or_create_entity(db, name="Patriots Union", entity_type="thinktank", country_code="GB")
    db.commit()
    text = "The Patriots Union released a report in London. Britain officials commented."
    mentions = match_entities_in_text(db, text, min_confidence=0.6)
    # 只有 GB（1.0）保留；US（0.5）被过滤
    assert len(mentions) == 1
    assert mentions[0].country_code == "GB"


def test_match_entities_in_text_blacklist_dampens_confidence(db, redis_client):
    """黑名单命中降权 ×0.3：单实体别名入 Redis 黑名单后 confidence=1.0×0.3=0.3 → needs_review=True。"""
    from app.agenda_engine.config import get_agenda_settings
    settings = get_agenda_settings()

    find_or_create_entity(
        db, name="Washington Post", entity_type="gov_body", country_code="US",
    )
    db.commit()

    # 把 alias 写入 Redis 黑名单（SADD entity:blacklist "washington post"）
    redis_client.sadd(settings.entity_blacklist_key, "washington post")
    try:
        mentions = match_entities_in_text(
            db,
            "The Washington Post issued a statement.",
            redis_client=redis_client,
        )
        assert len(mentions) == 1
        assert mentions[0].confidence == pytest.approx(0.3)
        assert mentions[0].needs_review is True
    finally:
        redis_client.srem(settings.entity_blacklist_key, "washington post")


def test_match_entities_in_text_no_entities_returns_empty(db):
    """空实体库：返回空列表，不报错。"""
    assert match_entities_in_text(db, "Joe Biden spoke.") == []


def test_match_entities_in_text_empty_text_returns_empty(db):
    """空文本：返回空列表。"""
    find_or_create_entity(db, name="Joe Biden", entity_type="person", country_code="US")
    db.commit()
    assert match_entities_in_text(db, "") == []


# ---------- update_first_utterances ----------


def test_update_first_utterances_appends_and_keeps_sorted(db):
    """追加 3 条乱序 occurred_at → 自动按 occurred_at 升序排序。"""
    entity = find_or_create_entity(db, name="Joe Biden", entity_type="person", country_code="US")
    db.commit()

    art1, art2, art3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    update_first_utterances(
        db, entity.id, article_id=art2, quote="q2", occurred_at=T0 + timedelta(days=2),
        detection_method="llm_first_utterance", model="m", prompt_version="v1",
    )
    update_first_utterances(
        db, entity.id, article_id=art1, quote="q1", occurred_at=T0,
        detection_method="llm_first_utterance", model="m", prompt_version="v1",
    )
    update_first_utterances(
        db, entity.id, article_id=art3, quote="q3", occurred_at=T0 + timedelta(days=5),
        detection_method="llm_first_utterance", model="m", prompt_version="v1",
    )
    db.commit()
    db.expire_all()
    refreshed = db.get(PersonOrg, entity.id)
    history = refreshed.first_utterances
    assert len(history) == 3
    occurred = [r["occurred_at"] for r in history]
    assert occurred == sorted(occurred), "必须保持 occurred_at 升序"
    # 顺序：art1 < art2 < art3
    assert history[0]["article_id"] == str(art1)
    assert history[1]["article_id"] == str(art2)
    assert history[2]["article_id"] == str(art3)


def test_update_first_utterances_idempotent_by_article_id(db):
    """同 article_id 重复追加 → 幂等去重（worker 重试安全）。"""
    entity = find_or_create_entity(db, name="Joe Biden", entity_type="person", country_code="US")
    db.commit()
    art = uuid.uuid4()
    update_first_utterances(
        db, entity.id, article_id=art, quote="q", occurred_at=T0,
        detection_method="llm_first_utterance", model="m", prompt_version="v1",
    )
    update_first_utterances(
        db, entity.id, article_id=art, quote="q-dup", occurred_at=T0,
        detection_method="llm_first_utterance", model="m", prompt_version="v1",
    )
    db.commit()
    db.expire_all()
    refreshed = db.get(PersonOrg, entity.id)
    assert len(refreshed.first_utterances) == 1
    assert refreshed.first_utterances[0]["quote"] == "q"  # 保留先写为准


def test_update_first_utterances_rejects_empty_quote(db):
    """空 quote 直接报错（无依据表述不允许入库）。"""
    entity = find_or_create_entity(db, name="Joe Biden", entity_type="person", country_code="US")
    db.commit()
    with pytest.raises(ValueError):
        update_first_utterances(
            db, entity.id, article_id=uuid.uuid4(), quote="  ", occurred_at=T0,
            detection_method="llm_first_utterance", model="m", prompt_version="v1",
        )


def test_update_first_utterances_missing_entity_raises(db):
    """实体不存在 → KeyError。"""
    with pytest.raises(KeyError):
        update_first_utterances(
            db, uuid.uuid4(), article_id=uuid.uuid4(), quote="q", occurred_at=T0,
            detection_method="llm_first_utterance", model="m", prompt_version="v1",
        )


def test_update_first_utterances_record_fields_complete(db):
    """记录字段：article_id/quote/occurred_at/detection_method/model/prompt_version/created_at 全。"""
    entity = find_or_create_entity(db, name="Joe Biden", entity_type="person", country_code="US")
    db.commit()
    art = uuid.uuid4()
    update_first_utterances(
        db, entity.id, article_id=art, quote="We propose a new deal", occurred_at=T0,
        detection_method="llm_first_utterance", model="Qwen2.5-0.5B-Instruct",
        prompt_version="first-utterance-v1",
    )
    db.commit()
    db.expire_all()
    record = db.get(PersonOrg, entity.id).first_utterances[0]
    assert record["article_id"] == str(art)
    assert record["quote"] == "We propose a new deal"
    assert record["occurred_at"] == T0.isoformat()
    assert record["detection_method"] == "llm_first_utterance"
    assert record["model"] == "Qwen2.5-0.5B-Instruct"
    assert record["prompt_version"] == "first-utterance-v1"
    assert "created_at" in record
