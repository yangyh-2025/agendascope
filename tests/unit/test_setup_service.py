"""安装向导服务单元测试（T5.6）：国家码归一化等纯函数（DB 路径见集成测试）。"""
import pytest

from app.services.setup_service import normalize_countries


class TestNormalizeCountries:
    def test_uppercase_and_dedupe(self):
        assert normalize_countries(["us", "JP", "us", " cn "]) == ["US", "JP", "CN"]

    def test_order_preserved(self):
        assert normalize_countries(["jp", "us", "de"]) == ["JP", "US", "DE"]

    def test_invalid_code_rejected(self):
        for bad in (["USA"], ["U1"], [""], ["U"], ["美国"]):
            with pytest.raises(ValueError):
                normalize_countries(bad)
