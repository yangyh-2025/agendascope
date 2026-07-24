"""NLP 基础管线包（Phase 2 M2-1）。

语言识别（fastText lid.176）→ 跨语言向量化（paraphrase-multilingual-mpnet-base-v2）
→ pgvector 落库与相似度检索 → Elasticsearch 全文索引同步 → 延迟埋点。
模型权重统一放仓库根 models/ 目录（.gitignore 排除，部署时单独分发），路径经 NLP_ 环境变量可配。

CPU 实测基线（开发机，2026-07-24 记录于 CHANGELOG）：见 CHANGELOG Phase 2 分节。
"""
