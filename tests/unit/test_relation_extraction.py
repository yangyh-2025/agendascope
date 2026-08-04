"""关系抽取核心逻辑单元测试（不调真实 LLM，全 mock）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.relation_extraction import (
    EXPIRE_THRESHOLD,
    RelationExtractor,
    confidence_score,
    decay_confidence,
)


def test_confidence_score_levels():
    assert confidence_score("high") == 0.9
    assert confidence_score("medium") == 0.7
    assert confidence_score("low") == 0.4
    assert confidence_score("unknown") == 0.5  # fallback


def test_decay_confidence_no_decay_at_zero_days():
    now = datetime.now(UTC)
    base = 0.9
    assert abs(decay_confidence(base, now, now) - base) < 0.001


def test_decay_confidence_half_life_at_tau():
    """τ=14 天后 confidence 应约为 base/e ≈ 0.37 × base。"""
    now = datetime.now(UTC)
    last_seen = now - timedelta(days=14)
    result = decay_confidence(0.9, last_seen, now)
    assert 0.30 < result < 0.35  # exp(-1) ≈ 0.368, 0.9*0.368 ≈ 0.331


def test_decay_confidence_two_tau():
    """28 天后（2τ），confidence 应约 0.135 × base。"""
    now = datetime.now(UTC)
    last_seen = now - timedelta(days=28)
    result = decay_confidence(0.9, last_seen, now)
    assert 0.10 < result < 0.14


def test_decay_confidence_future_last_seen():
    """last_seen 在未来（异常），days 取 0，不衰减。"""
    now = datetime.now(UTC)
    future = now + timedelta(days=1)
    assert decay_confidence(0.9, future, now) == 0.9


def test_decay_confidence_below_threshold_triggers_expire():
    """56 天后（4τ），0.9 × exp(-4) ≈ 0.016 < 0.2 阈值。"""
    now = datetime.now(UTC)
    last_seen = now - timedelta(days=56)
    result = decay_confidence(0.9, last_seen, now)
    assert result < EXPIRE_THRESHOLD


def test_decay_confidence_naive_datetime_handled():
    """无时区的 last_seen_at 不抛错。"""
    now = datetime.now(UTC)
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7)
    result = decay_confidence(0.9, naive, now)
    assert 0 < result < 0.9
