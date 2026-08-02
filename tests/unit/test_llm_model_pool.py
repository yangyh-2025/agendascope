"""模型池（ModelPool）单元测试：调度/限流/熔断/失败转移。

用 stub HTTP 服务端验证 OpenAICompatibleEngine 的池模式：
- 多模型配置下请求按 in-flight 最少调度
- 单模型失败 → 熔断 → 请求转其他模型
- 全部失败 → 抛 LLMParseError
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import BaseModel, Field

from app.llm.api_engine import OpenAICompatibleEngine
from app.llm.model_pool import ModelPool, PoolModel
from app.llm.settings import LLMSettings


class NameOutput(BaseModel):
    name: str = Field(max_length=60)
    confidence: str = Field(pattern=r"^(high|medium|low)$")


class _PoolStubServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.request_log: list[dict] = []
        self.fail_countdown: int = 0  # 前 N 次返回 500
        self.call_count: int = 0


class _FakePoolHandler(BaseHTTPRequestHandler):
    server: _PoolStubServer

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        srv = self.server
        srv.call_count += 1
        srv.request_log.append({"path": self.path, "model": body.get("model")})
        if srv.fail_countdown > 0:
            srv.fail_countdown -= 1
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"server error"}')
            return
        payload = {"choices": [{"message": {"content": '{"name":"测试","confidence":"high"}'}}]}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *args):
        pass


@pytest.fixture()
def pool_stub():
    server = _PoolStubServer(("127.0.0.1", 0), _FakePoolHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def make_engine(pool_stub, models):
    """构造使用指定 stub 服务的引擎（两个模型指向同一 stub，便于计数）。"""
    port = pool_stub.server_address[1]
    settings = LLMSettings(profile="api", request_timeout_seconds=10)
    pool = ModelPool(
        settings=settings,
        models=[
            PoolModel(
                name=name, base_url=f"http://127.0.0.1:{port}/v1",
                api_key="", model=model, max_concurrency=2, qps=100,
            )
            for name, model in models
        ],
        cooldown_s=0,  # 测试立即恢复
    )
    engine = OpenAICompatibleEngine(settings)
    engine._pool = pool  # 注入测试用 pool（替代引擎内部自建的 legacy pool）
    return engine, pool


def test_pool_round_robin_across_models(pool_stub):
    """两个可用模型：请求分散到两个模型（in-flight 最少调度）。"""
    engine, _ = make_engine(pool_stub, [("m1", "model-a"), ("m2", "model-b")])
    for _ in range(4):
        result, _ = engine.generate_structured("s", "u", NameOutput, max_retries=0)
        assert result.name == "测试"
    models_hit = {r["model"] for r in pool_stub.request_log}
    assert models_hit == {"model-a", "model-b"}


def test_pool_failover_on_500(pool_stub):
    """首模型失败（500）→ 熔断 → 请求转第二模型。"""
    pool_stub.fail_countdown = 1  # 仅第 1 次 500（m1 失败），m2 立即成功
    engine, _ = make_engine(pool_stub, [("m1", "model-a"), ("m2", "model-b")])
    for _ in range(3):
        result, _ = engine.generate_structured("s", "u", NameOutput, max_retries=0)
        assert result.name == "测试"
    # m1 第一次失败（熔断），第 2 次请求应打到 m2
    assert pool_stub.request_log[0]["model"] == "model-a"
    assert pool_stub.request_log[1]["model"] == "model-b"


def test_pool_all_fail_raises(pool_stub):
    """全部模型失败 → 抛 LLMUnavailableError（上层降级链捕获 LLMError）。"""
    pool_stub.fail_countdown = 100
    engine, _ = make_engine(pool_stub, [("m1", "model-a")])
    with pytest.raises(Exception) as excinfo:
        engine.generate_structured("s", "u", NameOutput, max_retries=0)
    # 触发上层降级链的错误类型：LLMParseError 或 LLMUnavailableError（均 LLMError 子类）
    from app.llm.errors import LLMError

    assert isinstance(excinfo.value, LLMError)


def test_pool_legacy_single_when_not_configured(pool_stub):
    """未配置 LLM_POOL → 走单模型路径（不要求 pool 语义）。"""
    port = pool_stub.server_address[1]
    settings = LLMSettings(profile="api", api_base_url=f"http://127.0.0.1:{port}/v1")
    engine = OpenAICompatibleEngine(settings)
    engine.load()
    result, _ = engine.generate_structured("s", "u", NameOutput, max_retries=0)
    assert result.name == "测试"
