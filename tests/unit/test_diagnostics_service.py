"""诊断包脱敏单元测试（T5.13）：敏感键/URL 凭据脱敏为 ***。"""
from app.services.diagnostics_service import redact_config


class TestRedactConfig:
    def test_sensitive_keys_masked(self):
        out = redact_config({
            "jwt_secret_key": "super-secret",
            "seed_admin_password": "Admin12345",
            "collector_internal_token": "tok",
            "llm_api_key": "sk-xxx",
            "app_name": "AgendaScope",
            "app_port": 8000,
        })
        assert out["jwt_secret_key"] == "***"
        assert out["seed_admin_password"] == "***"
        assert out["collector_internal_token"] == "***"
        assert out["llm_api_key"] == "***"
        assert out["app_name"] == "AgendaScope"
        assert out["app_port"] == 8000

    def test_url_embedded_credential_masked(self):
        out = redact_config({
            "database_url": "postgresql+psycopg2://agenda:agenda_dev_pwd@localhost:5432/agendascope",
            "redis_url": "redis://:redis_pwd@localhost:6379/0",
        })
        assert "agenda_dev_pwd" not in out["database_url"]
        assert "agenda:***@localhost" in out["database_url"]
        assert "redis_pwd" not in out["redis_url"]

    def test_nested_dict_recursed(self):
        out = redact_config({"outer": {"api_key": "k", "plain": "v"}})
        assert out["outer"]["api_key"] == "***"
        assert out["outer"]["plain"] == "v"

    def test_non_string_scalars_kept(self):
        out = redact_config({"threshold": 0.85, "enabled": True, "none_val": None})
        assert out == {"threshold": 0.85, "enabled": True, "none_val": None}
