"""0006_alerts_subscriptions：站内信已读时间 + 订阅推送 + 报告导出排队状态（T4.14/T4.16/T4.17）

- alerts.read_at：站内信标记已读时间（GET /alerts、POST /alerts/{id}/read 配套）
- subscriptions：用户 × 国家 × 议题分类 日报/周报订阅（unsubscribe_token 免登录退订）
- subscription_deliveries：每期投递记录（发送失败退避队列 + 日终失败报告数据源）
- report_exports.status CHECK 扩展 'pending'（并发 >3 排队态）
"""
from alembic import op

revision = "0006_alerts_subscriptions"
down_revision = "0005_final_review_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ")

    op.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
        id UUID PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id),
        country_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        topic_category VARCHAR(50),
        frequency VARCHAR(10) NOT NULL DEFAULT 'daily',
        locale VARCHAR(10) NOT NULL DEFAULT 'zh-CN',
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        unsubscribe_token VARCHAR(128) NOT NULL UNIQUE,
        last_sent_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_subscriptions_frequency CHECK (frequency IN ('daily','weekly'))
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id)")

    op.execute("""
    CREATE TABLE IF NOT EXISTS subscription_deliveries (
        id UUID PRIMARY KEY,
        subscription_id UUID NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
        period VARCHAR(10) NOT NULL,
        period_date DATE NOT NULL,
        status VARCHAR(12) NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_retry_at TIMESTAMPTZ,
        error TEXT,
        sent_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_deliveries_status CHECK (status IN ('pending','sent','failed')),
        CONSTRAINT uq_delivery_scope UNIQUE (subscription_id, period, period_date)
    )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_deliveries_retry "
        "ON subscription_deliveries(status, next_retry_at)"
    )

    # 报告导出并发 >3 排队态：status CHECK 扩展 'pending'
    op.execute("ALTER TABLE report_exports DROP CONSTRAINT IF EXISTS ck_exports_status")
    op.execute("""
    ALTER TABLE report_exports
    ADD CONSTRAINT ck_exports_status
    CHECK (status IN ('pending','processing','done','failed'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE report_exports DROP CONSTRAINT IF EXISTS ck_exports_status")
    op.execute("""
    ALTER TABLE report_exports
    ADD CONSTRAINT ck_exports_status
    CHECK (status IN ('processing','done','failed'))
    """)
    op.execute("DROP TABLE IF EXISTS subscription_deliveries")
    op.execute("DROP TABLE IF EXISTS subscriptions")
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS read_at")
