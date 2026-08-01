"""LLM 服务配置：双配置档（估算，详细设计 6.2 / 开发计划 T2.12）。

- ``gpu-24g``  ：1×24GB GPU 档，推荐 Qwen2.5-14B-Instruct-GPTQ-Int4（或 7B），float16/cuda，单议题 P95 目标 ≤10s
- ``cpu-quant``：CPU 量化档，推荐 Qwen2.5-3B-Instruct（int8/GGUF 转换后落 models/ 目录），单议题 P95 目标 ≤60s
- ``cpu-dev``  ：开发/测试默认档，Qwen2.5-0.5B-Instruct float32，CPU 可直接真实推理
- ``api``     ：调用外部 OpenAI 兼容 API（如 Qwen API / DeepSeek / 通义千问 / 本地 vLLM）
  通过 LLM_API_BASE_URL + LLM_API_KEY + LLM_API_MODEL 三个环境变量配置；
  不依赖本地模型权重，数据经 HTTPS 传出——适用于内网 API 网关或公有云部署

切换方式：环境变量 ``LLM_PROFILE``；模型目录可用 ``LLM_MODEL_DIR`` 覆盖（相对路径基于仓库根目录）。
模型权重不进 git（根目录 ``models/`` 已被 .gitignore 排除），部署时单独分发。
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

# 配置档：model_dir 相对仓库根目录；device 为空表示自动（cuda 可用则 cuda，否则 cpu）
PROFILES: dict[str, dict[str, object]] = {
    "gpu-24g": {
        "model_dir": "models/Qwen2.5-14B-Instruct-GPTQ-Int4",
        "device": "cuda",
        "torch_dtype": "float16",
        "max_new_tokens": 384,
        "request_timeout_seconds": 10,
    },
    "cpu-quant": {
        "model_dir": "models/Qwen2.5-3B-Instruct",
        "device": "cpu",
        "torch_dtype": "float32",
        "max_new_tokens": 256,
        "request_timeout_seconds": 60,
    },
    "cpu-dev": {
        "model_dir": "models/Qwen2.5-0.5B-Instruct",
        "device": "cpu",
        "torch_dtype": "float32",
        "max_new_tokens": 192,
        "request_timeout_seconds": 60,
    },
    "api": {
        # API 模式：本地无模型权重，调用远程 OpenAI 兼容端点
        "model_dir": "",  # 不需要本地模型
        "device": "",
        "torch_dtype": "",
        "max_new_tokens": 512,
        "request_timeout_seconds": 120,
    },
}


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_", env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    profile: str = "api"  # 默认云 API（LLM_PROFILE=api）；本地模式可显式设为 cpu-dev/cuda
    model_dir: str = ""  # 空则按 profile 默认
    device: str = ""  # 空则自动检测
    torch_dtype: str = ""  # 空则按 profile 默认
    max_new_tokens: int = 0  # 0 则按 profile 默认
    request_timeout_seconds: int = 0  # 0 则按 profile 默认

    # API 模式配置（profile=api 时生效，OpenAI 兼容端点）
    api_base_url: str = ""   # 如 https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key: str = ""        # 留空则读 LLM_API_KEY 环境变量；也可直接写 .env
    api_model: str = ""      # 如 qwen-max / qwen-plus / deepseek-chat / gpt-4o
    max_concurrency: int = 2  # API 并发上限（线程信号量；讯飞星辰 QPS 2/并发 2 对齐）

    max_context_tokens: int = 2000  # 命名 prompt 上下文预算（估算，T2.13）
    queue_maxsize: int = 1000
    queue_batch_size: int = 8
    queue_batch_window_ms: int = 50

    failure_rate_threshold: float = 0.2  # 推理超时/失败率 >20% 触发降级（详细设计 6.2）
    health_window_size: int = 20
    health_min_samples: int = 5
    alert_debounce_seconds: int = 3600

    categories: str = ""  # JSON 数组，覆盖默认主题分类体系（部署方可扩展，T2.14）

    # 命名 worker（app.worker.naming_worker）：聚类待命名议题 → LLM 标注回填
    naming_worker_batch_size: int = 20  # 每轮拉取的待命名议题上限
    naming_worker_poll_seconds: float = 30.0  # 无待命名议题时的轮询间隔
    naming_worker_retry_cooldown_seconds: float = 600.0  # 单点降级议题的重试冷却（避免每轮重复判定刷留痕）

    # ---- 便捷属性 ----
    @property
    def is_api_mode(self) -> bool:
        return self.profile == "api"

    def _profile_defaults(self) -> dict[str, object]:
        if self.profile not in PROFILES:
            raise ValueError(f"未知 LLM 配置档: {self.profile}（可选: {sorted(PROFILES)}）")
        return PROFILES[self.profile]

    def resolved_model_dir(self) -> Path:
        if self.is_api_mode:
            return REPO_ROOT / "models"  # API 模式不需要模型目录，返回哨兵路径
        raw = self.model_dir or str(self._profile_defaults()["model_dir"])
        path = Path(raw)
        return path if path.is_absolute() else REPO_ROOT / path

    def resolved_device(self) -> str:
        if self.is_api_mode:
            return "api"
        if self.device:
            return self.device
        configured = str(self._profile_defaults()["device"])
        if configured == "cuda":
            try:
                import torch
                return "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                return "cpu"
        return "cpu"

    def resolved_torch_dtype(self) -> str:
        return self.torch_dtype or str(self._profile_defaults()["torch_dtype"])

    def resolved_max_new_tokens(self) -> int:
        return self.max_new_tokens or int(str(self._profile_defaults()["max_new_tokens"]))

    def resolved_request_timeout(self) -> int:
        return self.request_timeout_seconds or int(str(self._profile_defaults()["request_timeout_seconds"]))

    def resolved_model_name(self) -> str:
        """返回用于 llm_judgements 留痕的模型名。API 模式优先用 api_model 字段。"""
        if self.is_api_mode and self.api_model:
            return self.api_model
        if self.is_api_mode:
            return "api-model"
        return self.resolved_model_dir().name


@lru_cache
def get_llm_settings() -> LLMSettings:
    return LLMSettings()
