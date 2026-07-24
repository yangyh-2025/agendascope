"""聚类引擎包（Phase 2 M2-2）。

双策略聚类（BERTopic 主线 + Agglomerative 硬阈值并行评估）、在线增量双阈值归簇、
每小时全局重聚类校正 + Redis 快照发布、topics/topic_articles 落库与生命周期初版、
BERTopic 不可用时关键词匹配降级链。

议题命名/分类/摘要由 LLM 服务（app.llm，独立开发）消费 service.py 暴露的接口完成。
"""

# NLP 管线向量化落库后投递的流：cluster worker 消费入口（聚类接在向量化之后）
STREAM_EMBEDDED_ARTICLES = "nlp:embedded"
