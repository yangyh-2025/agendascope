"""0008_fix_stale_status_checks：清除 0001 遗留的旧版自动命名 CHECK 约束

0001_init 用裸 SQL 建表，status CHECK 由 PostgreSQL 自动命名为
sources_status_check / report_exports_status_check。0006/0007 扩展 status
取值（'pending'/'disabled'）时按模型层 ck_* 命名 DROP CONSTRAINT IF EXISTS，
静默未命中，导致旧窄约束与新宽约束并存，旧约束仍拒绝 'pending'/'disabled'。
本迁移删除两个旧约束（新 ck_* 宽约束已在位，语义不受影响）。
"""
from alembic import op

revision = "0008_fix_stale_status_checks"
down_revision = "0007_setup_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_status_check")
    op.execute("ALTER TABLE report_exports DROP CONSTRAINT IF EXISTS report_exports_status_check")


def downgrade() -> None:
    # 以与 ck_* 一致的宽定义恢复旧命名约束（恢复窄定义会与存量 disabled/pending 数据冲突）
    op.execute("""
    ALTER TABLE sources
    ADD CONSTRAINT sources_status_check
    CHECK (status IN ('active','degraded','failed','disabled'))
    """)
    op.execute("""
    ALTER TABLE report_exports
    ADD CONSTRAINT report_exports_status_check
    CHECK (status IN ('pending','processing','done','failed'))
    """)
