"""降级监控单元测试（T2.12/T2.16）：失败率 >20% 判降级，回落自动恢复。"""
from app.llm.health import DegradationMonitor
from app.llm.settings import LLMSettings


def _monitor() -> DegradationMonitor:
    return DegradationMonitor(LLMSettings(health_window_size=10, health_min_samples=5, failure_rate_threshold=0.2))


def test_healthy_below_min_samples():
    monitor = _monitor()
    for _ in range(3):
        assert monitor.record(False) is False
    assert monitor.degraded is False, "样本不足不判降级"


def test_degrades_when_failure_rate_above_threshold():
    monitor = _monitor()
    monitor.record(True)
    flipped = False
    for _ in range(4):
        flipped = monitor.record(False, reason="timeout") or flipped
    assert flipped, "5 样本中 4 失败(80%) 应触发降级翻转"
    assert monitor.degraded is True
    assert monitor.degraded_since is not None
    assert monitor.reason


def test_no_flip_when_failure_rate_at_threshold():
    monitor = _monitor()
    for _ in range(4):
        monitor.record(True)
    assert monitor.record(False) is False, "1/5=20% 不超过阈值，不降级"
    assert monitor.degraded is False


def test_recovers_when_failure_rate_falls_back():
    monitor = _monitor()
    monitor.record(True)
    for _ in range(4):
        monitor.record(False)
    assert monitor.degraded is True
    # 滑动窗口(10)内持续成功，失败样本被挤出窗口后自动恢复
    flipped = False
    for _ in range(9):
        flipped = monitor.record(True) or flipped
    assert flipped and monitor.degraded is False
    assert monitor.degraded_since is None


def test_mark_unavailable_immediate_degradation():
    monitor = _monitor()
    assert monitor.mark_unavailable("模型目录不存在") is True
    assert monitor.degraded is True
    assert "模型目录不存在" in monitor.reason
    assert monitor.mark_unavailable("再次失败") is False, "已降级不重复翻转"


def test_mark_recovered():
    monitor = _monitor()
    monitor.mark_unavailable("x")
    assert monitor.mark_recovered() is True
    assert monitor.degraded is False
    assert monitor.mark_recovered() is False
