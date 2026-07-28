"""0007_setup_state：安装向导状态 + 企业许可 + 源 disabled 态（T5.6/T5.10）

- setup_state：安装向导 KV 持久化（基础配置/监控范围/完成步骤/initialized 标记），
  修复向导 Step 2/3 只回显不落库的假保存问题
- system_license：企业授权码登记（授权码只存 SHA-256 哈希，不落明文；
  payload 为签发方声明的 license_id/product/expires_at）
- sources.status CHECK 扩展 'disabled'：监控范围未勾选国家的源置 disabled，
  调度器只调度 active/degraded，天然停止采集
"""
from alembic import op

revision = "0007_setup_state"
down_revision = "0006_alerts_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS setup_state (
        key VARCHAR(50) PRIMARY KEY,
        value JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS system_license (
        id UUID PRIMARY KEY,
        code_hash CHAR(64) NOT NULL UNIQUE,
        payload JSONB NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        activated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        activated_by UUID REFERENCES users(id)
    )
    """)

    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS ck_sources_status")
    op.execute("""
    ALTER TABLE sources
    ADD CONSTRAINT ck_sources_status
    CHECK (status IN ('active','degraded','failed','disabled'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS ck_sources_status")
    op.execute("""
    ALTER TABLE sources
    ADD CONSTRAINT ck_sources_status
    CHECK (status IN ('active','degraded','failed'))
    """)
    op.execute("DROP TABLE IF EXISTS system_license")
    op.execute("DROP TABLE IF EXISTS setup_state")
