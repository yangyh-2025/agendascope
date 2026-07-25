"""议程引擎 worker 集成冒烟测试：单轮真实跑通消亡扫描、次日归并、实体黑名单。

不 Mock：用 tests/conftest.py 的 db（agendascope_test）与 redis_client（db14）夹具，
造真实 articles/topics 行验证 worker 三类周期任务实际写库行为。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.agenda_engine.entity_blacklist import get_entity_blacklist
from app.models.topic import Topic
from app.worker.agenda_worker import AgendaWorker
from tests.conftest import make_source
from tests.integration.conftest import make_article

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _make_topic(db, **kwargs) -> Topic:
    defaults = {
        "name": "worker 冒烟议题",
        "name_auto": "worker 冒烟议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["冒烟"],
        "country_scope": ["CN"],
        "lifecycle_state": "forming",
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


async def test_run_once_triggers_all_three_tasks(db, redis_client):
    """首轮（_last_* 均为 0）：sweep + merge + blacklist 三任务都真实执行。"""
    # 消亡候选：10 天前 last_seen_at，应被 sweep 归档
    stale_topic = _make_topic(
        db, name="将消亡议题", last_seen_at=datetime.now(UTC) - timedelta(days=10),
    )
    # 实体黑名单数据源：≥1 篇近 30 天文章
    source = make_source(db, country_code="CN", language="zh")
    make_article(db, source, title="拜登冒烟测试讲话", content="拜登 冒烟", language="zh")
    db.commit()

    worker = AgendaWorker(redis_client=redis_client)
    done = worker.run_once()
    assert done == 3, "首轮三任务应全部触发"

    # sweep：stale_topic 已归档
    db.expire_all()
    assert db.get(Topic, stale_topic.id).lifecycle_state == "archived"

    # blacklist：Redis Set 已写入
    blacklist = get_entity_blacklist(redis_client)
    assert "拜登" in blacklist


async def test_run_once_respects_intervals(db, redis_client):
    """把 _last_* 设置为当前时刻，本轮三任务都不再触发（间隔未到）。"""
    import time

    worker = AgendaWorker(redis_client=redis_client)
    worker._last_merge = time.monotonic()
    worker._last_sweep = time.monotonic()
    worker._last_blacklist = time.monotonic()
    assert worker.run_once() == 0


async def test_individual_triggers(db, redis_client):
    """--merge-once / --sweep-once / --blacklist-once 单独触发语义。"""
    source = make_source(db, country_code="CN", language="zh")
    make_article(db, source, title="拜登单触发测试", content="拜登", language="zh")
    db.commit()

    worker = AgendaWorker(redis_client=redis_client)
    # 单独触发 blacklist（其他两个不动）
    worker._last_blacklist = 0.0
    assert worker.maybe_refresh_blacklist() is True
    assert "拜登" in get_entity_blacklist(redis_client)

    # sweep 单独触发：无 stale 议题不影响
    worker._last_sweep = 0.0
    assert worker.maybe_sweep() is True

    # merge 单独触发：无候选不报错
    worker._last_merge = 0.0
    assert worker.maybe_merge() is True
