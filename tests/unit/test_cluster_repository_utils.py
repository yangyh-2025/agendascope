"""质心时间衰减池化与生命周期口径单元测试（纯函数，无需基础设施）。"""
import math

import pytest

from app.clustering.repository import lifecycle_for_size, time_decay_pool


def test_lifecycle_for_size_thresholds():
    assert lifecycle_for_size(1, confirmed_min_size=10) == "nascent"   # 孤证微簇
    assert lifecycle_for_size(2, confirmed_min_size=10) == "forming"
    assert lifecycle_for_size(9, confirmed_min_size=10) == "forming"
    assert lifecycle_for_size(10, confirmed_min_size=10) == "confirmed"


def test_time_decay_pool_recent_evidence_weighs_old_centroid():
    old = [1.0, 0.0]
    new = [0.0, 1.0]
    # 距上次更新极短：旧质心权重 ≈1，新证据几乎不影响
    pooled = time_decay_pool(old, new, dt_hours=0.01, half_life_hours=24)
    assert pooled[0] > 0.99
    # 距上次更新一个半衰期：新旧各占一半（归一化后相等）
    pooled = time_decay_pool(old, new, dt_hours=24, half_life_hours=24)
    assert pooled[0] == pooled[1]


def test_time_decay_pool_output_normalized():
    pooled = time_decay_pool([0.6, 0.8], [0.8, 0.6], dt_hours=12, half_life_hours=24)
    assert math.sqrt(sum(v * v for v in pooled)) == pytest.approx(1.0)


def test_title_fingerprint_normalizes_case_and_punctuation():
    """标题指纹：小写 + 去标点/空白，转载改写（仅大小写/标点差异）应归一为同一指纹。"""
    from app.clustering.online import title_fingerprint

    assert title_fingerprint("The Central Bank Cuts Rates!") == "thecentralbankcutsrates"
    assert title_fingerprint("the central bank cuts rates...") == "thecentralbankcutsrates"
    assert title_fingerprint("  The Central Bank Cuts Rates  ") == "thecentralbankcutsrates"
    # 不同标题 → 不同指纹
    assert title_fingerprint("A faraway planet discovered") != "thecentralbankcutsrates"
    # 空/None → 空指纹
    assert title_fingerprint(None) == ""
    assert title_fingerprint("") == ""
