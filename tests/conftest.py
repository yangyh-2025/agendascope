"""pytest 共享夹具。

- 单元测试：无需基础设施，直接运行
- 集成测试：需要本地 docker compose 的 postgres(5432)/redis(6379) 可达；
  使用独立测试库 agendascope_test 与 redis db14，避免污染开发数据
"""
import os
import subprocess
import sys
import uuid

import pytest

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, os.path.abspath(BACKEND_DIR))

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://agenda:agenda_dev_pwd@localhost:5432/agendascope_test",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/14")

os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("REDIS_URL", TEST_REDIS_URL)
os.environ.setdefault("REDIS_STREAM_URL", TEST_REDIS_URL)
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")
os.environ.setdefault("COLLECTOR_INTERNAL_TOKEN", "test-internal-token")
os.environ.setdefault("SEED_ADMIN_USERNAME", "admin")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "Admin12345")
os.environ.setdefault("LICENSE_SECRET_KEY", "test-license-secret")

# 隔离 LLM 相关 env：测试用显式注入的 settings，避免开发机 .env 的 LLM_POOL/LLM_API_*
# 污染单元测试（pydantic-settings 会读仓库根 .env；env 变量优先级高于 env_file，
# 故置空串可覆盖 .env 值）。LLM_MAX_CONCURRENCY 是 int 字段，空串会校验失败，pop 掉走默认。
for _k in (
    "LLM_POOL", "LLM_API_BASE_URL", "LLM_API_KEY", "LLM_API_MODEL", "LLM_PROFILE",
):
    os.environ[_k] = ""
os.environ.pop("LLM_MAX_CONCURRENCY", None)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


def _admin_url() -> str:
    return TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"


def _db_reachable() -> bool:
    try:
        engine = create_engine(_admin_url(), connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _redis_reachable() -> bool:
    try:
        import redis

        return bool(redis.Redis.from_url(TEST_REDIS_URL, socket_connect_timeout=3).ping())
    except Exception:
        return False


@pytest.fixture(scope="session")
def migrated_db():
    """建测试库并执行 alembic upgrade head；基础设施不可达时跳过集成测试。"""
    if not _db_reachable():
        pytest.skip("本地 PostgreSQL 不可达（需先 docker compose up -d db）")
    db_name = TEST_DATABASE_URL.rsplit("/", 1)[1]
    engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": db_name}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    env = dict(os.environ, DATABASE_URL=TEST_DATABASE_URL)
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND_DIR, env=env, check=True, capture_output=True,
    )
    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(migrated_db):
    """每测试独立会话 + 全表清空的隔离环境。"""
    SessionLocal = sessionmaker(bind=migrated_db)
    session = SessionLocal()
    with migrated_db.connect() as conn:
        for table in (
            "system_license", "setup_state",
            "subscription_deliveries", "subscriptions", "report_exports",
            "alerts", "alert_rules", "topic_articles", "agenda_snapshots", "topics",
            "collection_jobs", "articles", "sources", "users",
            "persons_orgs", "llm_judgements",
        ):
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        conn.commit()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def redis_client():
    if not _redis_reachable():
        pytest.skip("本地 Redis 不可达（需先 docker compose up -d redis）")
    import redis

    client = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    for pattern in ("dedup:*", "session:*", "bl:*", "rl:*", "alert:*"):
        keys = list(client.scan_iter(match=pattern, count=200))
        if keys:
            client.delete(*keys)
    yield client
    client.close()


@pytest.fixture()
def admin_user(db):
    from app.services.seed_service import ensure_admin

    return ensure_admin(db)


@pytest.fixture()
def client(db, redis_client):
    """FastAPI TestClient，DB 会话与 Redis 指向测试环境。"""
    from fastapi.testclient import TestClient

    import app.db.redis_client as redis_module
    from app.db.session import get_db
    from app.main import app

    test_redis = redis_client

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    redis_module._cache_client = test_redis
    redis_module._stream_client = test_redis

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    redis_module.reset_clients()


@pytest.fixture()
def auth_headers(client, admin_user, db):
    db.commit()
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin12345"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    # 初始管理员 must_change_password=True：先走改密闭环，业务接口才放行（T1.7）
    chg = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "Admin12345", "new_password": "AdminNew123"},
        headers=headers,
    )
    assert chg.status_code == 200, chg.text
    return headers


def make_source(db, **overrides) -> "object":
    from app.models.source import Source

    defaults = {
        "name": f"Test Media {uuid.uuid4().hex[:6]}",
        "country_code": "US",
        "homepage_url": "https://example.com",
        "feed_url": f"https://example.com/feed-{uuid.uuid4().hex[:8]}.xml",
        "collect_mode": "rss",
        "adapter_type": "rss",
        "media_type": "online",
        "language": "en",
        "poll_interval_min": 5,
        "audience_weight": 10.0,
    }
    defaults.update(overrides)
    source = Source(**defaults)
    db.add(source)
    db.flush()
    return source
