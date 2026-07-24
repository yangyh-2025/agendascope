"""0003_llm：LLM 判定留痕表 + topics 扩展模型/prompt 版本字段（T2.17）

- 新表 llm_judgements：每次 LLM 判定记录 模型名 + prompt_version + 输入/输出
  快照 + 成败 + 耗时（详细设计 3.2 关键不变量③：记录模型名与 prompt 版本，
  支持换 prompt 后批量重跑对比，PRD 8.3-6）；
- topics 增加 llm_model / prompt_version 两列：议题当前名称/分类/摘要由哪个
  模型与哪版 prompt 产出，随 topics 行直接可查，详细历史查 llm_judgements。
"""
from alembic import op

revision = "0003_llm"
down_revision = "0002_pipeline_latency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE llm_judgements (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        topic_id       UUID         REFERENCES topics(id),   -- 议题相关判定; 其他模块(如事件终审)判定可为空
        task_type      VARCHAR(30)  NOT NULL
                       CHECK (task_type IN ('topic_naming','topic_category','topic_summary')),
        model_name     VARCHAR(100) NOT NULL,                -- 判定所用模型名(如 Qwen2.5-0.5B-Instruct)
        prompt_version VARCHAR(50)  NOT NULL,                -- 判定所用 prompt 版本(如 topic-naming-v1)
        input_payload  JSONB        NOT NULL,                -- 输入快照(代表标题/top 词/议题名); rerun 行含 rerun_of 基线 id
        output_payload JSONB,                                -- 结构化输出; 失败为 NULL
        success        BOOLEAN      NOT NULL,
        naming_method  VARCHAR(20),                          -- llm / ctfidf_fallback(降级兜底)
        error          TEXT,
        latency_ms     INTEGER,
        created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX idx_llm_judgements_topic ON llm_judgements(topic_id, created_at DESC)")
    op.execute("CREATE INDEX idx_llm_judgements_task ON llm_judgements(task_type, prompt_version, created_at DESC)")
    op.execute("ALTER TABLE topics ADD COLUMN llm_model VARCHAR(100)")
    op.execute("ALTER TABLE topics ADD COLUMN prompt_version VARCHAR(50)")
    op.execute("COMMENT ON TABLE llm_judgements IS 'LLM 判定留痕(模型名+prompt版本+输入输出快照), 支持历史判定批量重跑对比'")
    op.execute("COMMENT ON COLUMN topics.llm_model IS '最近一次 LLM 判定所用模型名(T2.17)'")
    op.execute("COMMENT ON COLUMN topics.prompt_version IS '最近一次 LLM 判定所用 prompt 版本(T2.17)'")


def downgrade() -> None:
    op.execute("ALTER TABLE topics DROP COLUMN prompt_version")
    op.execute("ALTER TABLE topics DROP COLUMN llm_model")
    op.execute("DROP TABLE IF EXISTS llm_judgements")
