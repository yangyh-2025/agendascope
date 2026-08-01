"""0009_embedding_dim_1024：嵌入列维度改为 1024（云 API 嵌入 bge-m3 维度）

背景：嵌入向量化从本地 sentence-transformers（768 维）切换到云 API 嵌入
（自托管 BAAI/bge-m3，1024 维，OpenAI 兼容 /embeddings 端点）。
pgvector 列维度是类型的一部分，必须与写入向量精确一致。本迁移：
  1) articles.embedding / topics.centroid 从 vector(768) 改为 vector(1024)
     （与 bge-m3 输出维度对齐，见 NLP_EMBEDDING_DIM=1024）；
  2) 清空旧向量（维度不一致，存量 768 维向量不可用；新采集文章经 nlp_worker 自动重算）；
  3) 重建两个 HNSW 余弦索引（列维度变更后旧索引失效）。

注意：本地 mpnet 模式（NLP_EMBEDDING_PROFILE=local）下 768 维向量无法写入
vector(1024) 列——pgvector 要求精确维度匹配。故切换云嵌入（bge-m3）前，本地模式
仅作为对照不可混用；测试夹具已同步改用 1024 维（DIM=1024）。
"""
from alembic import op

revision = "0009_embedding_dim_1024"
down_revision = "0008_fix_stale_status_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 维度改为 1024（先清空避免维度过期行阻塞）
    op.execute("UPDATE articles SET embedding = NULL WHERE embedding IS NOT NULL")
    op.execute("UPDATE topics SET centroid = NULL WHERE centroid IS NOT NULL")
    op.execute("ALTER TABLE articles ALTER COLUMN embedding TYPE vector(1024)")
    op.execute("ALTER TABLE topics ALTER COLUMN centroid TYPE vector(1024)")

    # 2) 重建 HNSW 余弦索引（旧索引列类型已变，drop 后重建）
    op.execute("DROP INDEX IF EXISTS idx_articles_embedding")
    op.execute("DROP INDEX IF EXISTS idx_topics_centroid")
    op.execute(
        "CREATE INDEX idx_articles_embedding ON articles USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX idx_topics_centroid ON topics USING hnsw (centroid vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # 3) 更新列注释（原 0001 写死"768 维"）
    op.execute("COMMENT ON COLUMN articles.embedding IS '跨语言向量, pgvector 存储, HNSW 索引供在线增量归簇近邻检索; 云嵌入 bge-m3 1024 维'")
    op.execute("COMMENT ON COLUMN topics.centroid IS '议题质心(加权池化, 维度与 articles.embedding 一致)'")


def downgrade() -> None:
    # 回滚到 768：同样需清空（1024 维向量无法写入 vector(768)）并重建索引
    op.execute("UPDATE articles SET embedding = NULL WHERE embedding IS NOT NULL")
    op.execute("UPDATE topics SET centroid = NULL WHERE centroid IS NOT NULL")
    op.execute("DROP INDEX IF EXISTS idx_articles_embedding")
    op.execute("DROP INDEX IF EXISTS idx_topics_centroid")
    op.execute("ALTER TABLE articles ALTER COLUMN embedding TYPE vector(768)")
    op.execute("ALTER TABLE topics ALTER COLUMN centroid TYPE vector(768)")
    op.execute(
        "CREATE INDEX idx_articles_embedding ON articles USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX idx_topics_centroid ON topics USING hnsw (centroid vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
