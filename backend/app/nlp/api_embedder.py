"""OpenAI 兼容 API 嵌入引擎（ApiEmbedder）。

当 NLP_EMBEDDING_PROFILE=api 时，通过三个环境变量接入远程嵌入服务：
  NLP_EMBEDDING_API_BASE_URL — OpenAI 兼容端点（如 https://api.openai.com/v1
                               或通义 https://dashscope.aliyuncs.com/compatible-mode/v1）
  NLP_EMBEDDING_API_KEY      — API 密钥（空则无鉴权，适用本地 vLLM/one-api 网关）
  NLP_EMBEDDING_API_MODEL    — 嵌入模型名（如 text-embedding-3-small / text-embedding-v3）

接口与本地 Embedder 对齐：embed(texts) -> list[list[float]]（L2 归一化）。
维度不做硬编码——首次请求从 API 返回的向量长度自动识别并缓存，
pgvector 列维度由迁移脚本按同一规则对齐（见 alembic 迁移）。

调用失败抛 EmbeddingUnavailableError，触发上层降级链（绝不静默）。
"""
from __future__ import annotations

import time

import httpx

from app.core.logging import get_logger
from app.nlp.config import NlpSettings, get_nlp_settings

logger = get_logger("nlp.embedding_api")


class EmbeddingUnavailableError(RuntimeError):
    """嵌入 API 不可用（未配置 / 鉴权失败 / 服务端错误）。"""


class ApiEmbedder:
    """OpenAI 兼容 API 嵌入引擎——替代本地 sentence-transformers Embedder。"""

    def __init__(self, settings: NlpSettings | None = None):
        self.settings = settings or get_nlp_settings()
        self._client: httpx.Client | None = None
        self._dim: int | None = None  # 首次响应自动识别，缓存
        self._load_error: str | None = None

    # ------------------------------------------------------------------
    # 生命周期（接口对齐本地 Embedder）
    # ------------------------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        return self._client is not None

    @property
    def dim(self) -> int:
        """已识别的嵌入维度；未识别时触发一次连通性请求。"""
        if self._dim is None:
            self.load()
            self._identify_dim()
        assert self._dim is not None
        return self._dim

    def load(self) -> None:
        """初始化 httpx 客户端（幂等）。"""
        if self._client is not None:
            return
        base_url = self.settings.embedding_api_base_url
        if not base_url:
            self._load_error = "NLP_EMBEDDING_API_BASE_URL 未配置（NLP_EMBEDDING_PROFILE=api 时必须设置）"
            raise EmbeddingUnavailableError(self._load_error)
        api_key = self.settings.embedding_api_key
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        timeout = httpx.Timeout(60.0, connect=10.0)
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)
        self._load_error = None
        logger.info("embedding_api_client_ready", base_url=base_url)

    def _identify_dim(self) -> None:
        """发一次最小请求识别向量维度（texts=[''] 返回 1 个向量）。"""
        vectors = self._embed_request(["ping"])
        if not vectors or not vectors[0]:
            raise EmbeddingUnavailableError("嵌入 API 返回空向量，无法识别维度")
        self._dim = len(vectors[0])
        logger.info("embedding_api_dim_identified", dim=self._dim)

    def unload(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # 嵌入（接口对齐本地 Embedder）
    # ------------------------------------------------------------------
    def _embed_request(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            self.load()
        assert self._client is not None
        body = {
            "model": self.settings.embedding_api_model,
            "input": texts,
        }
        resp = self._client.post("/embeddings", json=body)
        if resp.status_code in (401, 403):
            self._load_error = f"嵌入 API 鉴权失败 ({resp.status_code}): {resp.text[:200]}"
            raise EmbeddingUnavailableError(self._load_error)
        if resp.status_code >= 500:
            self._load_error = f"嵌入 API 服务端错误 ({resp.status_code}): {resp.text[:200]}"
            raise EmbeddingUnavailableError(self._load_error)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        items.sort(key=lambda it: it.get("index", 0))
        return [[float(v) for v in item["embedding"]] for item in items]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入，返回 L2 归一化向量列表（维度与 API 一致）。"""
        if not texts:
            return []
        started = time.monotonic()
        vectors = self._embed_request(texts)
        # 归一化：cosine 检索依赖 L2 归一化（与本地 Embedder normalize_embeddings=True 对齐）
        normalized: list[list[float]] = []
        for vec in vectors:
            norm = sum(v * v for v in vec) ** 0.5
            normalized.append([v / norm for v in vec] if norm else vec)
        if self._dim is None and vectors:
            self._dim = len(vectors[0])
        elapsed = time.monotonic() - started
        logger.info("embedding_api_batch", texts=len(texts), dim=self._dim, elapsed_s=round(elapsed, 2))
        return normalized

    def embed_article(self, title: str, summary: str | None, content: str | None) -> list[float]:
        from app.nlp.embedding import build_embedding_text

        return self.embed([build_embedding_text(title, summary, content)])[0]


__all__ = ["ApiEmbedder", "EmbeddingUnavailableError"]
