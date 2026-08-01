"""配置管理（pydantic-settings，.env 与代码分离）。

启动防护：app_env 非 dev/test 时，若 JWT 密钥 / DB 口令 / 内部 token / 初始管理员密码
仍为仓库默认值，Settings 实例化直接失败（拒绝启动），避免弱默认配置上生产。
"""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_ENVS = ("dev", "test")
_DEFAULT_JWT_SECRET = "change-me-to-a-long-random-string"
_DEFAULT_INTERNAL_TOKEN = "change-me-internal-collector-token"
_DEFAULT_DB_PASSWORD = "agenda_dev_pwd"
_DEFAULT_SEED_ADMIN_PASSWORD = "Admin12345"


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

    jwt_secret_key: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    refresh_token_expire_minutes: int = 720

    collector_internal_token: str = _DEFAULT_INTERNAL_TOKEN

    # 离线/在线模式开关（T1.2）：开启后禁止 GDELT 等外联通道，调度器跳过一切外部拉取与巡检探测
    offline_mode: bool = False

    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 30
    default_crawl_interval_seconds: int = 900
    gdelt_enabled: bool = True
    gdelt_interval_seconds: int = 900
    gdelt_api_base: str = "https://api.gdeltproject.org/api/v2/doc/doc"
    gdelt_countries: str = (
        "US,GB,CN,JP,RU,DE,FR,KR,TR,QA,CA,AU,ES,IN,BR,ZA,NG,EG,IR,IL,SA,AE,ID,"
        "MY,SG,TH,VN,PH,MX,AR,PL,SE,NO,CH,NL,BE,CL,CO,PE,MA,GH,TZ,UG,KZ,NZ"
    )
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
    # 源健康巡检（T1.23）：24h 采集成功率低于该阈值主动告警；国家覆盖率低于阈值触发 P0 告警
    source_success_rate_alert_threshold: float = 0.95
    country_coverage_alert_threshold: float = 0.70

    login_rate_limit_per_minute: int = 5  # T1.8：5 次/分钟/IP
    login_lock_threshold: int = 5
    login_lock_minutes: int = 15

    seed_admin_username: str = "admin"
    seed_admin_password: str = _DEFAULT_SEED_ADMIN_PASSWORD

    # 企业许可（T5.10）：HMAC-SHA256 验签密钥，部署方/厂商持有；留空则无法录入授权码
    license_secret_key: str = ""

    # 应用日志文件输出（T5.10 日志查看/诊断包）：留空仅 stdout；配置后启用滚动文件
    log_file_path: str = ""
    log_file_max_mb: int = 10
    log_file_backup_count: int = 5

    # 磁盘超阈值自动清理（T5.10）：>85% 时清理 90 天前原始 HTML/缓冲文件并告警
    disk_cleanup_threshold_percent: float = 85.0
    raw_html_retention_days: int = 90
    raw_html_dir: str = "data/raw_html"

    @model_validator(mode="after")
    def _reject_weak_defaults_outside_dev(self) -> "Settings":
        """非 dev/test 环境拒绝弱默认配置启动（等保基线）。"""
        if self.app_env in _DEV_ENVS:
            return self
        problems: list[str] = []
        if self.jwt_secret_key == _DEFAULT_JWT_SECRET or self.jwt_secret_key.startswith("change-me"):
            problems.append("JWT_SECRET_KEY 仍为默认值")
        if _DEFAULT_DB_PASSWORD in self.database_url:
            problems.append("DATABASE_URL 仍使用默认口令 agenda_dev_pwd")
        if self.collector_internal_token == _DEFAULT_INTERNAL_TOKEN or self.collector_internal_token.startswith("change-me"):
            problems.append("COLLECTOR_INTERNAL_TOKEN 仍为默认值")
        if self.seed_admin_password == _DEFAULT_SEED_ADMIN_PASSWORD:
            problems.append("SEED_ADMIN_PASSWORD 仍为默认值 Admin12345")
        if problems:
            raise ValueError(
                f"当前环境 APP_ENV={self.app_env} 检测到弱默认配置，拒绝启动："
                + "；".join(problems)
                + "。请在 .env 中替换为强随机值后重启。"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
