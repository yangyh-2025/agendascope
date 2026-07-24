"""配置管理（pydantic-settings，.env 与代码分离）。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "AgendaScope"
    app_env: str = "dev"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    database_url: str = "postgresql+psycopg2://agenda:agenda_dev_pwd@localhost:5432/agendascope"
    redis_url: str = "redis://localhost:6379/0"
    redis_stream_url: str = "redis://localhost:6379/1"
    elasticsearch_url: str = "http://localhost:9200"

    jwt_secret_key: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    refresh_token_expire_minutes: int = 720

    collector_internal_token: str = "change-me-internal-collector-token"

    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 30
    default_crawl_interval_seconds: int = 900
    gdelt_enabled: bool = True
    gdelt_interval_seconds: int = 900
    gdelt_api_base: str = "https://api.gdeltproject.org/api/v2/doc/doc"
    gdelt_countries: str = "US,GB,CN,JP,RU,DE,FR,KR,TR,QA,CA,AU,ES"
    gdelt_max_records: int = 50
    gdelt_buffer_dir: str = "data/gdelt_buffer"

    rsshub_base: str = "http://localhost:1200"

    collect_api_base: str = "http://localhost:8000"

    global_site_proxy: str = ""
    cn_site_proxy: str = ""

    crawl_timeout_seconds: int = 30
    crawl_max_retries: int = 3
    crawl_user_agent: str = "AgendaScopeBot/1.0 (+https://agendascope.local/bot)"

    source_fail_rate_alert_threshold: float = 0.10
    source_fail_rate_window_hours: int = 24

    login_rate_limit_per_minute: int = 10
    login_lock_threshold: int = 5
    login_lock_minutes: int = 15

    seed_admin_username: str = "admin"
    seed_admin_password: str = "Admin12345"


@lru_cache
def get_settings() -> Settings:
    return Settings()
