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
        # 多模型池：per-model 限流（并发+QPS）+ 调度 + 失败转移 + 熔断。
        # 未配置 LLM_POOL 时退化为单模型（兼容 LLM_API_* 配置）。
        from app.llm.model_pool import ModelPool

        self._pool = ModelPool(settings=self.settings)
        self._model_clients: dict[str, httpx.Client] = {}  # per-model 客户端缓存
        # 单模型模式下的 QPS 限流状态（ModelPool 未配置时兜底）
        self._qps_lock = threading.Lock()
        self._last_request_ts = 0.0
        self._min_request_interval = 0.5
        self._min_interval_floor = 0.35
        self._min_interval_ceiling = 1.5
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
        # 模型池模式下无单一客户端，以"池已配置"为准
        return self._client is not None or (self._pool.pool_configured and bool(self._pool.models))

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def model_dir_exists(self) -> bool:
        """API 模式不需要本地模型目录。"""
        return True

    def load(self) -> None:
        """初始化 httpx 客户端并先发一次空请求验证连通性（幂等）。

        模型池模式：不要求 LLM_API_BASE_URL（per-model 客户端惰性创建），
        仅标记 loaded；单模型模式：保持原有 base_url 校验。
        """
        if self.is_loaded:
            return
        if self._pool.pool_configured:
            self._load_error = None
            self._model_name = self._pool.models[0].model if self._pool.models else "api-model"
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
        for client in self._model_clients.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        self._model_clients.clear()

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

        模型池模式：从 ModelPool 调度最空闲模型，失败自动转移其他模型；
        单模型模式（无 LLM_POOL）：保持既有重试/降级语义。
        """
        started = time.monotonic()
        # 模型池模式：无单一客户端（per-model 客户端惰性创建），跳过单客户端校验
        if not self._pool.pool_configured:
            if self._client is None:
                self.load()
            client = self._client
            if client is None:
                raise LLMUnavailableError("API 客户端未初始化")

        schema_json = json.dumps(output_model.model_json_schema(), ensure_ascii=False)
        full_system = (
            f"{system}\n"
            "你必须只输出一个 JSON 对象，不要输出任何其他文字、解释或 markdown 代码块标记。\n"
            f"输出必须符合以下 JSON Schema：{schema_json}"
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": user},
        ]

        # 模型池模式：调度 + 失败转移（仅 LLM_POOL 配置了多模型时启用）
        if self._pool.pool_configured:
            return self._generate_via_pool(
                messages, output_model, max_retries, started,
            )

        # ---- 单模型模式（legacy LLM_API_*）----
        last_error: LLMParseError | None = None
        content = ""
        for attempt in range(max_retries + 1):
            try:
                body: dict[str, Any] = {
                    "model": self._model_name,
                    "messages": messages,
                    "temperature": 0.0,
                    "max_tokens": self.settings.resolved_max_new_tokens(),
                    "response_format": {"type": "json_object"},
                }
                # QPS 限流（复用 started 作"上次请求时刻"，不新增时钟调用）
                with self._qps_lock:
                    elapsed = started - self._last_request_ts
                    if elapsed < self._min_request_interval:
                        time.sleep(self._min_request_interval - elapsed)
                    self._last_request_ts = started
                resp = client.post("/chat/completions", json=body)
                # 自适应限流：QPS 超限退避
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

    # ------------------------------------------------------------------
    # 模型池模式（调度 + 失败转移 + 熔断）
    # ------------------------------------------------------------------
    def _generate_via_pool(
        self,
        messages: list[dict[str, str]],
        output_model: type[BaseModel],
        max_retries: int,
        started: float,
    ) -> tuple[Any, float]:
        """经 ModelPool 调度模型执行；失败自动转移其他模型。

        - 选最空闲可用模型（in-flight 最少）；
        - 并发+QPS 配额不足则等待重试（acquire 超时换下一模型）；
        - 单模型调用失败 → 记录熔断计数 + 换其他模型重试（每模型至多 1 次）；
        - 全部模型失败 → 抛 LLMParseError 触发既有降级链。
        """
        from app.llm.model_pool import PoolModel

        pool = self._pool
        tried: set[str] = set()      # 已成功 acquire 过的模型（去重）
        unavailable: set[str] = set()  # 本轮调用失败/熔断的模型（失败转移跳过）
        last_error: Exception | None = None

        # 最多尝试所有模型 × 每模型 retries 次
        for _ in range(max(1, len(pool.models) * (max_retries + 1))):
            model, ok = pool.acquire()
            if not ok or model.name in tried or model.name in unavailable:
                continue
            tried.add(model.name)
            try:
                client = self._client_for_model(model)
                body: dict[str, Any] = {
                    "model": model.model,
                    "messages": messages,
                    "temperature": 0.0,
                    "max_tokens": self.settings.resolved_max_new_tokens(),
                    "response_format": {"type": "json_object"},
                }
                resp = client.post("/chat/completions", json=body)
                if resp.status_code in (401, 403):
                    pool.release(model, success=False)
                    unavailable.add(model.name)
                    raise LLMUnavailableError(
                        f"API 鉴权失败 ({resp.status_code}) {model.name}: {resp.text[:200]}"
                    )
                if resp.status_code >= 500:
                    pool.release(model, success=False)
                    unavailable.add(model.name)
                    raise LLMUnavailableError(
                        f"API 服务端错误 ({resp.status_code}) {model.name}: {resp.text[:200]}"
                    )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                result = parse_structured(content, output_model)
                pool.release(model, success=True)
                return result, time.monotonic() - started
            except LLMParseError as exc:
                # 解析失败：模型本身正常（是输出格式问题），记录成功避免误熔断，
                # 但本轮判定失败（交给上层重试/降级语义）
                pool.release(model, success=True)
                last_error = exc
                break
            except LLMUnavailableError as exc:
                last_error = exc
                continue  # 换下一模型（unavailable 集合阻止再选该模型）
            except Exception as exc:
                pool.release(model, success=False)
                unavailable.add(model.name)
                last_error = LLMParseError(f"API 调用异常 {model.name}: {exc}")
                continue

        raise last_error or LLMParseError("结构化输出解析失败：模型池全部不可用")

    def _client_for_model(self, model: "PoolModel") -> httpx.Client:
        """为模型建/复用 OpenAI 兼容客户端（per-model base_url/key）。"""
        cache_key = model.base_url
        if cache_key in self._model_clients:
            return self._model_clients[cache_key]
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if model.api_key:
            headers["Authorization"] = f"Bearer {model.api_key}"
        timeout = httpx.Timeout(self.settings.resolved_request_timeout(), connect=10.0)
        client = httpx.Client(base_url=model.base_url.rstrip("/"), headers=headers, timeout=timeout)
        self._model_clients[cache_key] = client
        return client


__all__ = ["OpenAICompatibleEngine"]
