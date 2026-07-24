"""T2.5 pipeline_latency_sample 延迟埋点表（详细设计未给出 DDL，按 T2.5 口径自设计）。

published_at→visible_at 端到端延迟逐篇采样，按源/通道分桶，支撑延迟看板
（by_channel p95_min / 红线 >2h 违规明细，详细设计 1.16 监控接口数据口径）。
article_id 唯一约束：NLP worker 消费重放（Redis Streams 重投递）时幂等，不重复采样。

Revision ID: 0002_pipeline_latency
"""
from alembic import op

revision = "0002_pipeline_latency"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE pipeline_latency_sample (
        id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        article_id     UUID         NOT NULL REFERENCES articles(id),
        source_id      UUID         NOT NULL REFERENCES sources(id),
        channel        VARCHAR(10)  NOT NULL
                       CHECK (channel IN ('rss','rsshub','gdelt')),   -- 采集通道(对齐 articles.source_channel)
        country_code   CHAR(2)      NOT NULL,                          -- 冗余自 articles, 分桶聚合免 join
        published_at   TIMESTAMPTZ  NOT NULL,
        visible_at     TIMESTAMPTZ  NOT NULL,
        latency_ms     INT          NOT NULL CHECK (latency_ms >= 0),  -- visible_at-published_at, 时钟偏移负值收敛为 0
        latency_bucket VARCHAR(10)  NOT NULL
                       CHECK (latency_bucket IN ('<5m','5-15m','15-30m','30-60m','1-2h','>2h')),  -- 分桶对齐延迟红线 P95<=30min/>2h
        sampled_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE UNIQUE INDEX idx_pls_article ON pipeline_latency_sample(article_id)")
    op.execute("CREATE INDEX idx_pls_source_time ON pipeline_latency_sample(source_id, sampled_at DESC)")
    op.execute("CREATE INDEX idx_pls_channel_time ON pipeline_latency_sample(channel, sampled_at DESC)")
    op.execute("CREATE INDEX idx_pls_bucket_time ON pipeline_latency_sample(latency_bucket, sampled_at DESC)")
    op.execute("COMMENT ON TABLE pipeline_latency_sample IS '端到端延迟逐篇采样(T2.5): published_at→visible_at 按源/通道分桶; 延迟看板 by_channel p95 与 >2h 红线违规明细的数据源; article_id 唯一保证重投递幂等'")
    op.execute("COMMENT ON COLUMN pipeline_latency_sample.latency_bucket IS '分桶: <5m/5-15m/15-30m/30-60m/1-2h/>2h; 15-30m 贴近 P95 红线 30min, >2h 即红线违规桶'")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pipeline_latency_sample")
