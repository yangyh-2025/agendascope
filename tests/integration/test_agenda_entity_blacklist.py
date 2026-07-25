"""动态高频实体黑名单集成测试（T3.5）。

覆盖：refresh_entity_blacklist 全链路（真实 Postgres + Redis）、Top-K 排序、
TTL 设置、updated_at 时间戳、窗口外文章不计入、读侧接口契约、Redis 失败保旧值。
基础设施不可达时按 tests/conftest.py 的 skip 语义跳过。

种子词选择依据：jieba.posseg 实测（"拜登"=nrt=PEOPLE，"北京/上海/中国"=ns=LOCATION，
"突尼斯"=ns）——所选词均在默认词典，避免测试结果依赖词性标注的边界行为。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.entity_blacklist import (
    filter_blacklisted,
    get_entity_blacklist,
    is_blacklisted,
    refresh_entity_blacklist,
)
from tests.conftest import make_source
from tests.integration.conftest import make_article

pytestmark = pytest.mark.integration


def _seed_articles_with_entities(db) -> None:
    """构造一批近 30 天文章：高频 拜登/北京/上海（≥3 篇），低频 突尼斯（1 篇）。"""
    source = make_source(db, language="zh")
    now = datetime.now(UTC)

    high_freq_titles = [
        "拜登在北京与中国领导人会晤",
        "拜登出席上海合作组织峰会",
        "中美贸易谈判在北京举行，拜登发声",
        "上海进博会拜登致辞，北京方面表示欢迎",
        "北京冬奥会即将开幕，拜登表示关注",
    ]
    for i, title in enumerate(high_freq_titles):
        make_article(
            db,
            source,
            title=title,
            content="现场报道。",
            published_at=now - timedelta(days=i + 1),
        )
    # 低频：突尼斯 只在 1 篇出现
    make_article(
        db,
        source,
        title="突尼斯小城发生抗议",
        content="北非局势。",
        published_at=now - timedelta(days=2),
    )
    db.commit()


def test_refresh_entity_blacklist_writes_top_k_with_ttl(db, redis_client):
    _seed_articles_with_entities(db)
    settings = get_agenda_settings()

    result = refresh_entity_blacklist(db, redis_client, top_k=3, window_days=30)

    # Top-3：高频三巨头（拜登 5 篇 / 北京 4 篇 / 上海 2 篇，按频次降序）
    assert result[0] == "拜登"
    assert result[1] == "北京"
    assert result[2] == "上海"
    # 低频"突尼斯"被 Top-3 截断
    assert "突尼斯" not in result

    # Redis Set 与返回值一致
    stored = get_entity_blacklist(redis_client)
    assert stored == {"拜登", "北京", "上海"}

    # TTL 已设置且不超过配置上限
    ttl = redis_client.ttl(settings.entity_blacklist_key)
    assert 0 < ttl <= settings.entity_blacklist_ttl_hours * 3600

    # updated_at 时间戳已写入
    updated_at = redis_client.get(settings.entity_blacklist_updated_at_key)
    assert updated_at  # 非空 ISO 时间戳


def test_refresh_entity_blacklist_skips_articles_outside_window(db, redis_client):
    source = make_source(db, language="zh")
    now = datetime.now(UTC)

    # 窗口内：1 篇"北京"
    make_article(
        db, source,
        title="北京发布新政",
        content="内容。",
        published_at=now - timedelta(days=5),
    )
    # 窗口外：35 天前的"突尼斯"，不应进入统计
    make_article(
        db, source,
        title="突尼斯街头示威",
        content="内容。",
        published_at=now - timedelta(days=35),
    )
    db.commit()

    result = refresh_entity_blacklist(db, redis_client, top_k=10, window_days=30)

    assert "北京" in result
    assert "突尼斯" not in result  # 窗口外文章不计入


def test_get_and_is_blacklisted_reads_refreshed_set(db, redis_client):
    _seed_articles_with_entities(db)
    refresh_entity_blacklist(db, redis_client, top_k=3, window_days=30)

    assert is_blacklisted("拜登", redis_client) is True
    assert is_blacklisted("北京", redis_client) is True
    assert is_blacklisted("突尼斯", redis_client) is False


def test_filter_blacklisted_against_refreshed_set(db, redis_client):
    _seed_articles_with_entities(db)
    refresh_entity_blacklist(db, redis_client, top_k=3, window_days=30)

    kept = filter_blacklisted(["拜登", "突尼斯", "北京", "巴黎"], redis_client)
    # 高频"拜登/北京"被剔除；低频"突尼斯"与未出现的"巴黎"保留
    assert set(kept) == {"突尼斯", "巴黎"}


def test_refresh_failure_keeps_old_value(db, redis_client, monkeypatch):
    """Redis 写入异常时保旧值不抛错。"""
    _seed_articles_with_entities(db)
    # 先成功写一次作为旧值
    refresh_entity_blacklist(db, redis_client, top_k=3, window_days=30)
    old_snapshot = get_entity_blacklist(redis_client)
    assert old_snapshot

    # 让 pipeline.execute 抛错
    class _BrokenPipeline:
        def __init__(self, *_args, **_kwargs): ...
        def delete(self, *_args, **_kwargs): return self
        def sadd(self, *_args, **_kwargs): return self
        def expire(self, *_args, **_kwargs): return self
        def set(self, *_args, **_kwargs): return self
        def execute(self): raise ConnectionError("redis down")

    monkeypatch.setattr(redis_client, "pipeline", lambda transaction=True: _BrokenPipeline())

    # 不抛错，返回本次计算出的名单；Redis 里仍是旧值
    result = refresh_entity_blacklist(db, redis_client, top_k=3, window_days=30)
    assert isinstance(result, list)
    assert get_entity_blacklist(redis_client) == old_snapshot
