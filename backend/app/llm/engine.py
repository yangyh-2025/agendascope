"""transformers 本地推理引擎（T2.12）。

- 真实加载 Qwen 系列本地权重（models/ 目录），禁止 Mock 推理输出；
- 结构化输出：prompt 内嵌 JSON Schema 强约束 + 解析失败重试 1 次 + 单点降级（选型理由见 schemas.py）；
- 推理为同步阻塞调用，由 queue.py 放入独立线程执行，不阻塞 asyncio 主链路；
- 线程安全：加载与推理共用一把锁（CPU 单路推理串行最稳）。
"""
import threading
import time
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from app.llm.errors import LLMParseError, LLMUnavailableError
from app.llm.schemas import parse_structured
from app.llm.settings import LLMSettings, get_llm_settings

logger = structlog.get_logger(__name__)


class LLMEngine:
    """Qwen 系列本地推理引擎。"""

    def __init__(self, settings: LLMSettings | None = None):
        self.settings = settings or get_llm_settings()
        self._lock = threading.Lock()
        self._tokenizer: Any = None
        self._model: Any = None
        self._load_error: str | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    @property
    def model_name(self) -> str:
        return self.settings.resolved_model_dir().name

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def model_dir_exists(self) -> bool:
        path: Path = self.settings.resolved_model_dir()
        return (path / "config.json").exists()

    def load(self) -> None:
        """加载模型权重（幂等）。加载失败记录 load_error 并抛 LLMUnavailableError。"""
        with self._lock:
            if self._model is not None:
                return
            model_dir = self.settings.resolved_model_dir()
            if not self.model_dir_exists():
                self._load_error = f"模型目录不存在或缺少 config.json: {model_dir}"
                raise LLMUnavailableError(self._load_error)
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer

                dtype = getattr(torch, self.settings.resolved_torch_dtype())
                device = self.settings.resolved_device()
                started = time.monotonic()
                self._tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
                self._model = AutoModelForCausalLM.from_pretrained(str(model_dir), dtype=dtype)
                self._model.to(device)
                self._model.eval()
                self._load_error = None
                logger.info(
                    "llm_model_loaded", model=self.model_name, device=device,
                    dtype=str(dtype), elapsed_s=round(time.monotonic() - started, 2),
                )
            except Exception as exc:
                self._model = None
                self._tokenizer = None
                self._load_error = f"模型加载失败: {exc}"
                raise LLMUnavailableError(self._load_error) from exc

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._tokenizer = None

    # ------------------------------------------------------------------
    # 推理
    # ------------------------------------------------------------------
    def count_tokens(self, text: str) -> int:
        """统计 token 数；未加载时按 2 字符≈1 token 粗估（中英文混合的安全上界）。"""
        if self._tokenizer is not None:
            return len(self._tokenizer.encode(text))
        return max(1, len(text) // 2)

    def _generate(self, messages: list[dict[str, str]], max_new_tokens: int | None = None) -> str:
        if self._model is None or self._tokenizer is None:
            raise LLMUnavailableError(self._load_error or "模型未加载")
        import torch

        prompt_text = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._tokenizer(prompt_text, return_tensors="pt").to(self._model.device)
        budget = max_new_tokens or self.settings.resolved_max_new_tokens()
        with self._lock, torch.no_grad():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=budget,
                do_sample=False,  # 贪心解码：判定任务要求可复现，版本对比才有意义
                pad_token_id=self._tokenizer.eos_token_id,
            )
        new_ids = output_ids[0][inputs["input_ids"].shape[1]:]
        return str(self._tokenizer.decode(new_ids, skip_special_tokens=True))

    def generate_structured(
        self,
        system: str,
        user: str,
        output_model: type[BaseModel],
        max_retries: int = 1,
    ) -> tuple[Any, float]:
        """结构化生成：首次失败重试 max_retries 次（T2.12：解析失败重试 1 次后单点降级）。

        返回 (校验后的 pydantic 对象, 总耗时秒)。最终失败抛 LLMParseError/LLMUnavailableError。
        """
        started = time.monotonic()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: LLMParseError | None = None
        for attempt in range(max_retries + 1):
            raw = self._generate(messages)
            try:
                result = parse_structured(raw, output_model)
                return result, time.monotonic() - started
            except LLMParseError as exc:
                last_error = exc
                logger.warning(
                    "llm_parse_retry", attempt=attempt + 1, error=str(exc)[:200],
                )
                # 重试时把错误反馈进对话，引导模型修正输出格式
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"输出不符合要求：{exc}。请重新只输出符合 JSON Schema 的 JSON 对象。"},
                ]
        raise last_error or LLMParseError("结构化输出解析失败")
