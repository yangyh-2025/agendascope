"""NLP 管线配置（pydantic-settings，NLP_ 环境变量前缀，与主配置分离便于独立调参）。

- 模型权重默认读取仓库根 models/（.gitignore 排除），路径全部可配
- device: cpu（基线）/ cuda / auto（有 GPU 自动启用，即 T2.2 预留 GPU 开关）
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class NlpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NLP_", env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    model_dir: str = str(_REPO_ROOT / "models")
    lid_model_filename: str = "lid.176.bin"
    embedding_model: str = "paraphrase-multilingual-mpnet-base-v2"
    device: str = "cpu"  # cpu / cuda / auto
    embed_batch_size: int = 32
    lang_confidence_threshold: float = 0.8

    # 云嵌入 API 模式（NLP_EMBEDDING_PROFILE=api 时生效，OpenAI 兼容 /embeddings 端点；
    # 无需本地 sentence-transformers 权重，维度从 API 响应自动识别）
    embedding_profile: str = "api"  # api（云嵌入 bge-m3，默认）/ local（本地模型）
    embedding_api_base_url: str = ""  # 如 https://api.openai.com/v1 / dashscope compatible-mode/v1
    embedding_api_key: str = ""       # 留空则读 NLP_EMBEDDING_API_KEY 环境变量
    embedding_api_model: str = "text-embedding-3-small"

    es_url: str = ""  # 空则回落主配置 elasticsearch_url
    es_index: str = "agendascope_articles"
    es_max_retries: int = 5  # 指数退避上限(有界, 不死等); 超限整批重投递
    es_retry_backoff_seconds: float = 1.0
    es_sync_enabled: bool = True  # 低内存部署可关（NLP_ES_SYNC_ENABLED=false → 跳过 ES，搜索走 PG 降级）

    worker_group: str = "nlp"
    worker_batch_size: int = 32
    worker_block_ms: int = 5000
    worker_reclaim_idle_ms: int = 60000  # 滞留 pending 超此时长被回收重处理
    worker_max_attempts: int = 8  # 单消息处理尝试上限, 超限进死信

    @property
    def lid_model_path(self) -> Path:
        return Path(self.model_dir) / self.lid_model_filename

    @property
    def embedding_model_path(self) -> Path:
        return Path(self.model_dir) / "sentence-transformers" / self.embedding_model

    @property
    def hf_cache_dir(self) -> Path:
        return Path(self.model_dir) / "hf"


@lru_cache
def get_nlp_settings() -> NlpSettings:
    return NlpSettings()
