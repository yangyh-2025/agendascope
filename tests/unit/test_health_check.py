"""源健康巡检纯函数单元测试（T1.23）：重点源裁决与国家覆盖率计算。"""
from types import SimpleNamespace

from app.collector.health_check import compute_country_coverage, is_focus_source


class TestIsFocusSource:
    def test_high_frequency_is_focus(self):
        assert is_focus_source(1, None)
        assert is_focus_source(5, None)

    def test_normal_source_not_focus(self):
        assert not is_focus_source(15, None)
        assert not is_focus_source(60, {})

    def test_config_flag_marks_focus(self):
        assert is_focus_source(15, {"focus_source": True})

    def test_none_interval_defaults_to_focus(self):
        # None 按默认 5min 处理 → 重点源
        assert is_focus_source(None, None)


class TestComputeCountryCoverage:
    @staticmethod
    def _s(country, status="active"):
        return SimpleNamespace(country_code=country, status=status)

    def test_full_coverage(self):
        coverage, covered, uncovered = compute_country_coverage([self._s("US"), self._s("CN")])
        assert coverage == 1.0
        assert covered == ["CN", "US"]
        assert uncovered == []

    def test_partial_coverage(self):
        sources = [self._s("US"), self._s("GB", "failed"), self._s("CN", "degraded"), self._s("JP")]
        coverage, covered, uncovered = compute_country_coverage(sources)
        assert coverage == 0.5
        assert uncovered == ["CN", "GB"]

    def test_zz_pseudo_source_excluded(self):
        # GDELT 兜底伪源不冒充任何国家，不计入分母/分子
        coverage, covered, _ = compute_country_coverage([self._s("ZZ", "failed"), self._s("US")])
        assert coverage == 1.0
        assert covered == ["US"]

    def test_empty_sources_full_coverage(self):
        coverage, _, _ = compute_country_coverage([])
        assert coverage == 1.0
