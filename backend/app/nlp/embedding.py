"""sentence-transformers 向量化封装（T2.2）。

模型 paraphrase-multilingual-mpnet-base-v2（768 维，跨语言）：
- 批量推理（batch_size 可配），normalize_embeddings=True 使点积=cosine
- CPU 为基线；device=cuda/auto 即 GPU 开关（预留，ADR-005 预留 LaBSE 切换同理换 embedding_model 即可）
- 模型权重优先读本地 models/sentence-transformers/<模型名>，缺失时按 HF id 加载并缓存至 models/hf
"""
from app.core.logging import get_logger
from app.nlp.config import get_nlp_settings

logger = get_logger("nlp.embedding")

EMBEDDING_DIM = 768

# 送检正文截断：模型 max_seq_length=128 word pieces，长正文超出部分必然被截，
# 提前截断避免无效 tokenize 开销；标题完整保留（跨语言归簇的主信号）
_CONTENT_HEAD_CHARS = 1000


def build_embedding_text(title: str, summary: str | None, content: str | None) -> str:
    body = summary or (content or "")[:_CONTENT_HEAD_CHARS]
    return f"{title}\n{body}".strip()


def resolve_device(device: str) -> str:
    if device in ("cpu", "cuda"):
        return device
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


class Embedder:
    def __init__(self, model: str | None = None, device: str | None = None, batch_size: int | None = None):
        settings = get_nlp_settings()
        self.batch_size = batch_size or settings.embed_batch_size
        self.device = resolve_device(device or settings.device)
        from sentence_transformers import SentenceTransformer

        local_path = settings.embedding_model_path
        if model is None and local_path.exists():
            model_name = str(local_path)
            cache_folder = None
        else:
            model_name = model or settings.embedding_model
            cache_folder = str(settings.hf_cache_dir)
        self._model = SentenceTransformer(model_name, device=self.device, cache_folder=cache_folder)
        logger.info("embedding_model_loaded", model=model_name, device=self.device, batch_size=self.batch_size)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量推理，返回 L2 归一化 768 维向量（余弦检索直接可用）。"""
        if not texts:
            return []
        vectors = self._model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(v) for v in row] for row in vectors]

    def embed_article(self, title: str, summary: str | None, content: str | None) -> list[float]:
        return self.embed([build_embedding_text(title, summary, content)])[0]
