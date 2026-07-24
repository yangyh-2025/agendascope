"""LLM 服务配置：双配置档（估算，详细设计 6.2 / 开发计划 T2.12）。

- ``gpu-24g``  ：1×24GB GPU 档，推荐 Qwen2.5-14B-Instruct-GPTQ-Int4（或 7B），float16/cuda，单议题 P95 目标 ≤10s
- ``cpu-quant``：CPU 量化档，推荐 Qwen2.5-3B-Instruct（int8/GGUF 转换后落 models/ 目录），单议题 P95 目标 ≤60s
- ``cpu-dev``  ：开发/测试默认档，Qwen2.5-0.5B-Instruct float32，CPU 可直接真实推理

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
}


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_", env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    profile: str = "cpu-dev"
    model_dir: str = ""  # 空则按 profile 默认
    device: str = ""  # 空则自动检测
    torch_dtype: str = ""  # 空则按 profile 默认
    max_new_tokens: int = 0  # 0 则按 profile 默认
    request_timeout_seconds: int = 0  # 0 则按 profile 默认

    max_context_tokens: int = 2000  # 命名 prompt 上下文预算（估算，T2.13）
    queue_maxsize: int = 1000
    queue_batch_size: int = 8
    queue_batch_window_ms: int = 50

    failure_rate_threshold: float = 0.2  # 推理超时/失败率 >20% 触发降级（详细设计 6.2）
    health_window_size: int = 20
    health_min_samples: int = 5
    alert_debounce_seconds: int = 3600

    categories: str = ""  # JSON 数组，覆盖默认主题分类体系（部署方可扩展，T2.14）

    def _profile_defaults(self) -> dict[str, object]:
        if self.profile not in PROFILES:
            raise ValueError(f"未知 LLM 配置档: {self.profile}（可选: {sorted(PROFILES)}）")
        return PROFILES[self.profile]

    def resolved_model_dir(self) -> Path:
        raw = self.model_dir or str(self._profile_defaults()["model_dir"])
        path = Path(raw)
        return path if path.is_absolute() else REPO_ROOT / path

    def resolved_device(self) -> str:
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


@lru_cache
def get_llm_settings() -> LLMSettings:
    return LLMSettings()
