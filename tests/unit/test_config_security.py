"""Phase 1 配置加固单元测试：登录限流默认值 / offline_mode 开关 / 生产弱配置启动校验。"""
import pytest
from pydantic import ValidationError

from app.config import Settings

_STRONG = {
    "jwt_secret_key": "prod-jwt-secret-0123456789abcdef",
    "database_url": "postgresql+psycopg2://agenda:Str0ngDbPwdx@db:5432/agendascope",
    "collector_internal_token": "prod-internal-token-0123456789",
    "seed_admin_password": "Str0ngAdminx23",
}


class TestDefaults:
    def test_login_rate_limit_default_5(self):
        # T1.8：登录限流 5 次/分钟/IP（字段默认值，与环境变量无关）
        assert Settings.model_fields["login_rate_limit_per_minute"].default == 5

    def test_offline_mode_default_false(self):
        assert Settings.model_fields["offline_mode"].default is False

    def test_alert_threshold_defaults(self):
        assert Settings.model_fields["source_success_rate_alert_threshold"].default == 0.95
        assert Settings.model_fields["country_coverage_alert_threshold"].default == 0.70


class TestProductionWeakSecrets:
    def test_dev_and_test_allow_defaults(self):
        assert Settings(app_env="dev").app_env == "dev"
        assert Settings(app_env="test").app_env == "test"

    def test_prod_rejects_all_defaults(self):
        with pytest.raises(ValidationError) as exc:
            Settings(
                app_env="prod",
                jwt_secret_key="change-me-to-a-long-random-string",
                database_url="postgresql+psycopg2://agenda:agenda_dev_pwd@localhost:5432/agendascope",
                collector_internal_token="change-me-internal-collector-token",
                seed_admin_password="Admin12345",
            )
        msg = str(exc.value)
        assert "拒绝启动" in msg
        assert "JWT_SECRET_KEY" in msg
        assert "agenda_dev_pwd" in msg
        assert "COLLECTOR_INTERNAL_TOKEN" in msg
        assert "SEED_ADMIN_PASSWORD" in msg

    def test_prod_rejects_partial_weak(self):
        # 仅 JWT 密钥弱，其余强 —— 同样拒绝启动
        with pytest.raises(ValidationError) as exc:
            Settings(app_env="prod", **{**_STRONG, "jwt_secret_key": "change-me-x"})
        assert "JWT_SECRET_KEY" in str(exc.value)

    def test_staging_also_guarded(self):
        with pytest.raises(ValidationError):
            Settings(app_env="staging")

    def test_prod_accepts_strong_secrets(self):
        settings = Settings(app_env="prod", **_STRONG)
        assert settings.app_env == "prod"
