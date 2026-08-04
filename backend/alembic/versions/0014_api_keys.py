"""0014_api_keys：数据开放平台 API Key 表

数据开放平台（独立于主系统的 /developer 路由）使用 X-API-Key 头鉴权，
Key 与用户账号共用 users 表，但仅在创建时返回一次完整 Key，落库只存 sha256 hash。

字段：
  - prefix: 用于 UI 显示与日志（如 agk_Ab3xYz），不唯一索引（仅参考）
  - key_hash: sha256(plain_key) hex，唯一索引用于鉴权查询
  - scopes: JSON 数组，当前仅 'read'，预留给未来 write/admin
  - rate_limit_per_minute: 每 Key 每分钟限流，默认 60
  - revoked_at: 吊销时间戳，非 NULL 即失效（软删除）
"""
from alembic import op

revision = "0014_api_keys"
down_revision = "0013_drop_report_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE api_keys (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      name VARCHAR(100) NOT NULL,
      prefix VARCHAR(12) NOT NULL,
      key_hash CHAR(64) NOT NULL UNIQUE,
      scopes JSONB NOT NULL DEFAULT '["read"]'::jsonb,
      rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
      expires_at TIMESTAMPTZ,
      last_used_at TIMESTAMPTZ,
      revoked_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)
    op.execute("CREATE INDEX idx_api_keys_user ON api_keys(user_id)")
    op.execute("CREATE INDEX idx_api_keys_hash ON api_keys(key_hash)")
    op.execute("COMMENT ON TABLE api_keys IS '数据开放平台 API Key（X-API-Key 鉴权；存 sha256 hash，创建时返回一次完整 Key）'")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS api_keys CASCADE")
