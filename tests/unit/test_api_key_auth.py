"""API Key 生成/哈希/限流单元测试。"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.core.api_key_auth import generate_api_key, hash_api_key
from app.models.api_key import ApiKey


def test_generate_api_key_format():
    plain, key_hash, prefix = generate_api_key()
    assert plain.startswith("agk_")
    assert len(plain) > 40
    assert len(key_hash) == 64
    assert prefix.startswith("agk_")
    assert len(prefix) == 10
    assert hashlib.sha256(plain.encode()).hexdigest() == key_hash


def test_generate_api_key_uniqueness():
    keys = {generate_api_key()[0] for _ in range(50)}
    assert len(keys) == 50  # 全部唯一


def test_hash_api_key_deterministic():
    k = "agk_test123"
    assert hash_api_key(k) == hash_api_key(k)
    assert hash_api_key(k) == hashlib.sha256(k.encode()).hexdigest()


def test_hash_api_key_different_for_different_keys():
    assert hash_api_key("agk_a") != hash_api_key("agk_b")
