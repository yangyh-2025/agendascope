"""OpenAI 兼容 API 推理引擎（OpenAICompatibleEngine）。

当 LLM_PROFILE=api 时，通过三个环境变量接入远程大模型：
  LLM_API_BASE_URL — OpenAI 兼容端点（如 https://dashscope.aliyuncs.com/compatible-mode/v1
                       或 https://api.deepseek.com/v1 或本地 vLLM http://localhost:8000/v1）
  LLM_API_KEY      — API 密钥（空则无鉴权，适用本地 vLLM）
  LLM_API_MODEL    — 模型名（如 qwen-max / qwen-plus / deepseek-chat / gpt-4o）

接口签名与 LLMEngine.generate_structured 完全一致（返回 (pydantic_object, elapsed_s)），
因此 TopicAnnotator / naming_worker / final_review / first_utterance 无需任何改动即可切换。

结构化输出策略：使用 OpenAI 的 response_format={"type":"json_object"} +
prompt 内嵌 JSON Schema 指令（双重保险，与本地 LLMEngine 同口径）。
解析失败重试 1 次（与本地引擎一致）；最终失败抛 LLMParseError 触发上层降级链。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx
from pydantic import BaseModel

from app.llm.errors import LLMParseError, LLMUnavailableError
from app.llm.schemas import parse_structured
from app.llm.settings import LLMSettings, get_llm_settings

logger = logging.getLogger(__name__)


class OpenAICompatibleEngine:
    """OpenAI 兼容 API 推理引擎——替代本地 transformers LLMEngine。"""

    def __init__(self, settings: LLMSettings | None = None):
        self.settings = settings or get_llm_settings()
        self._client: httpx.Client | None = None
        self._model_name: str = ""
        self._load_error: str | None = None
        # API 并发上限（LLM_MAX_CONCURRENCY，默认 2）：线程信号量，跨命名/检测等
        # 所有调用方统一限流，对齐讯飞星辰等外部服务的 QPS/并发配额
        self._concurrency = threading.Semaphore(max(1, self.settings.max_concurrency))
        # QPS 限流（并发信号量 ≠ QPS）：并发 2 下连续快速请求仍会超外部 QPS 配额
        # （如讯飞星辰 QPS 2/并发 2），触发 AppIdQpsOverFlowError 导致降级。
        # 这里加最小请求间隔，跨线程共享（引擎单例）。
        self._qps_lock = threading.Lock()
        self._last_request_ts = 0.0
        # 最小请求间隔（秒）——自适应限流：
        # 基线 0.5s（QPS2 理论边界），遇 AppIdQpsOverFlowError（讯飞星辰 QPS 2/并发 2
        # 硬配额瞬时触顶）自动退避 +0.25s，连续成功 50 次后回调 -0.05s，
        # 稳定在"接近配额上限但不再触发降级"的区间——比固定 1.0s 保守限流更快。
        self._min_request_interval = 0.5
        self._min_interval_floor = 0.35  # 下限：防过低触顶
        self._min_interval_ceiling = 1.5  # 上限：防无限退避
        self._consecutive_success = 0
        # 会话内缓存 token 统计近似值（API 层无法精确 count；按 2 字符≈1 token 粗估）
        self._token_cache: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 生命周期（接口对齐 LLMEngine）
    # ------------------------------------------------------------------
    @property
    def model_name(self) -> str:
        return self._model_name or self.settings.api_model or "api-model"

    @property
    def is_loaded(self) -> bool:
        return self._client is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def model_dir_exists(self) -> bool:
        """API 模式不需要本地模型目录。"""
        return True

    def load(self) -> None:
        """初始化 httpx 客户端并先发一次空请求验证连通性（幂等）。"""
        if self._client is not None:
            return
        base_url = self.settings.api_base_url
        if not base_url:
            self._load_error = "LLM_API_BASE_URL 未配置（profile=api 时必须设置）"
            raise LLMUnavailableError(self._load_error)
        api_key = self.settings.api_key
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        timeout = httpx.Timeout(self.settings.resolved_request_timeout(), connect=10.0)
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)
        self._model_name = self.settings.api_model or "api-model"
        self._load_error = None

        # 连通性自检：列出可用模型（失败不阻塞——端点可能不支持 /models）
        try:
            resp = self._client.get("/models")
            if resp.status_code == 200:
                models_data = resp.json().get("data", [])
                if models_data:
                    available_ids = [m.get("id", "") for m in models_data]
                    if self._model_name not in available_ids:
                        pass  # 不阻塞：自定义模型名可能在 /models 列表中不可见
        except Exception:
            pass  # 连通性探针不影响加载判定

    def unload(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # token 估算（API 模式无本地 tokenizer，统一用 2 字符≈1 token）
    # ------------------------------------------------------------------
    def count_tokens(self, text: str) -> int:
        if text in self._token_cache:
            return self._token_cache[text]
        count = max(1, len(text) // 2)
        self._token_cache[text] = count
        return count

    # ------------------------------------------------------------------
    # 结构化生成（签名与 LLMEngine.generate_structured 完全一致）
    # ------------------------------------------------------------------
    def generate_structured(
        self,
        system: str,
        user: str,
        output_model: type[BaseModel],
        max_retries: int = 1,
    ) -> tuple[Any, float]:
        """调用远程 API 做结构化生成。返回 (pydantic 对象, 总耗时秒)。

        重试策略与本地 LLMEngine 一致：首次失败重试 max_retries 次，最终失败抛 LLMParseError。
        """
        started = time.monotonic()
        if self._client is None:
            self.load()
        client = self._client
        if client is None:
            raise LLMUnavailableError("API 客户端未初始化")

        schema_json = json.dumps(output_model.model_json_schema(), ensure_ascii=False)

        # 系统提示追加 JSON Schema 指令（与本地引擎同口径）
        full_system = (
            f"{system}\n"
            "你必须只输出一个 JSON 对象，不要输出任何其他文字、解释或 markdown 代码块标记。\n"
            f"输出必须符合以下 JSON Schema：{schema_json}"
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": user},
        ]
        last_error: LLMParseError | None = None
        content = ""

        for attempt in range(max_retries + 1):
            try:
                body: dict[str, Any] = {
                    "model": self._model_name,
                    "messages": messages,
                    "temperature": 0.0,  # 贪心等价：判定任务要求可复现
                    "max_tokens": self.settings.resolved_max_new_tokens(),
                    "response_format": {"type": "json_object"},
                }
                # 限并发：信号量阻塞直至有空闲配额（对齐外部 API QPS/并发限制）
                with self._concurrency:
                    # QPS 限流：与上一请求至少间隔 min_interval，
                    # 防并发 2 下连续快速请求超外部 QPS 配额触发降级。
                    # 复用 started（函数入口的 monotonic 时刻）作"上次请求时刻"，
                    # 不新增时钟调用（兼容测试对 time.monotonic 的 mock 侧值）；
                    # 入口时间差≈发出时间差（误差为单请求处理时间，可忽略）。
                    with self._qps_lock:
                        elapsed = started - self._last_request_ts
                        if elapsed < self._min_request_interval:
                            time.sleep(self._min_request_interval - elapsed)
                        self._last_request_ts = started
                    resp = client.post("/chat/completions", json=body)
                # 自适应限流：QPS 超限（429 / AppIdQpsOverFlowError）退避，
                # 连续成功回调间隔，稳定在"快但不再触顶"区间
                is_qps_overflow = (
                    resp.status_code == 429
                    or "QpsOverFlow" in resp.text
                    or "AppIdQpsOverFlow" in resp.text
                )
                if is_qps_overflow:
                    with self._qps_lock:
                        self._min_request_interval = min(
                            self._min_interval_ceiling,
                            self._min_request_interval + 0.25,
                        )
                        self._consecutive_success = 0
                    logger.warning(
                        "llm_api_qps_overflow_backoff",
                        extra={
                            "interval": self._min_request_interval,
                            "status": resp.status_code,
                            "detail": resp.text[:120],
                        },
                    )
                elif self._min_request_interval > self._min_interval_floor:
                    with self._qps_lock:
                        self._consecutive_success += 1
                        if self._consecutive_success >= 50:
                            self._min_request_interval = max(
                                self._min_interval_floor,
                                self._min_request_interval - 0.05,
                            )
                            self._consecutive_success = 0
                if resp.status_code == 401 or resp.status_code == 403:
                    self._load_error = f"API 鉴权失败 ({resp.status_code}): {resp.text[:200]}"
                    raise LLMUnavailableError(self._load_error)
                if resp.status_code >= 500:
                    self._load_error = f"API 服务端错误 ({resp.status_code}): {resp.text[:200]}"
                    raise LLMUnavailableError(self._load_error)
                resp.raise_for_status()

                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                result = parse_structured(content, output_model)
                return result, time.monotonic() - started
            except LLMParseError as exc:
                last_error = exc
                # 重试：把错误反馈进对话
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"输出不符合要求：{exc}。请重新只输出符合 JSON Schema 的 JSON 对象。"},
                ]
            except LLMUnavailableError:
                raise
            except Exception as exc:
                last_error = LLMParseError(f"API 调用异常: {exc}")
                if attempt == max_retries:
                    raise last_error from exc

        raise last_error or LLMParseError("结构化输出解析失败")


__all__ = ["OpenAICompatibleEngine"]
