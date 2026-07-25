"""动态高频实体黑名单单元测试（T3.5）。

纯函数测试：extract_entities 中英文 NER 抽取、is_valid_entity 过滤规则。
Redis / DB 集成路径在 tests/integration/test_agenda_entity_blacklist.py
（项目 requirements 未引入 fakeredis，遵循"禁 Mock"约束不伪造 Redis 客户端）。
"""
from __future__ import annotations

from app.agenda_engine.entity_extract import extract_entities, is_valid_entity

# ---------- extract_entities：中文 ----------


def test_extract_entities_chinese_mix():
    text = "美国总统拜登访华，中国外交部回应"
    entities = extract_entities(text)
    pairs = set(entities)
    texts = {t for t, _ in pairs}

    # jieba.posseg 实测："美国"=ns（地名）、"拜登"=nrt（人名转用）、"中国外交部"=nt（机构）
    # 中文机构名常被 jieba 整段切出（"中国外交部" 而非 "中国"+"外交部"），
    # 因此断言"文本里出现 LOCATION/PEOPLE/ORG 三类"以及关键实体被命中
    assert "美国" in texts
    assert "拜登" in texts
    # "中国"或"中国外交部"任一被识别（jieba 默认词典合并为后者）
    assert ("中国" in texts) or ("中国外交部" in texts)

    kinds = {k for _, k in pairs}
    assert "LOCATION" in kinds
    assert "PEOPLE" in kinds
    assert "ORG" in kinds


def test_extract_entities_chinese_kind_classification():
    entities: dict[str, str] = {}
    for entity_text, kind in extract_entities("拜登访问中国"):
        entities[entity_text] = kind
    assert entities.get("拜登") == "PEOPLE"
    assert entities.get("中国") == "LOCATION"


# ---------- extract_entities：英文 ----------


def test_extract_entities_english_persons_and_location():
    text = "Biden met with Xi Jinping in Beijing yesterday."
    entities = extract_entities(text)
    texts = {t for t, _ in entities}

    # 多词大写序列合并为一个实体（Xi Jinping），单词大写候选单独成实体（Biden/Beijing）
    assert "Xi Jinping" in texts
    assert "Biden" in texts
    assert "Beijing" in texts

    # Beijing 启发式归为 LOCATION
    kind_map = dict(entities)
    assert kind_map.get("Beijing") == "LOCATION"


def test_extract_entities_english_skips_sentence_leading_function_words():
    # 句首 The 不应被当作实体
    entities = extract_entities("The president spoke.")
    texts = {t for t, _ in entities}
    assert "The" not in texts


def test_extract_entities_org_suffix_classification():
    entities = extract_entities("The Foreign Ministry announced sanctions.")
    texts_kinds = dict(entities)
    # Ministry 后缀命中 ORG
    assert any(
        kind == "ORG" for text, kind in texts_kinds.items() if "Ministry" in text
    )


def test_extract_entities_empty_input():
    assert extract_entities("") == []


# ---------- is_valid_entity ----------


def test_is_valid_entity_filters_noise():
    assert is_valid_entity("中国") is True
    assert is_valid_entity("Biden") is True
    assert is_valid_entity("A") is False       # 单字符
    assert is_valid_entity("2024") is False    # 纯数字
    assert is_valid_entity("") is False
    assert is_valid_entity("!@#") is False     # 全标点
