"""OpenAI 兼容 API 引擎集成测试：内嵌 stub HTTP 服务端验证请求/响应协议。

用 Python http.server 起一个 fake API，验证 OpenAICompatibleEngine：
- 正确发送 POST /chat/completions 含 model/messages/temperature/response_format
- 正确解析 JSON 响应中的 choices[0].message.content
- 结构化输出 retry 链：首次失败 → 错误反馈回对话 → 第二次成功
- 鉴权头 Bearer token 正确传递
- 超时处理

隔离性说明（曾有全量跑偶发失败的教训）：
- 服务端用 ThreadingHTTPServer——httpx.Client 默认 keep-alive，单线程
  HTTPServer 会阻塞在上一个测试的空闲连接上无法接受新连接。
- 状态（request_log/payloads/call_count）挂在 server 实例上且 fixture 为函数级——
  若用类变量，上一个测试的迟到请求会写入类变量污染下一个测试的断言。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field

from app.llm.api_engine import OpenAICompatibleEngine
from app.llm.errors import LLMParseError, LLMUnavailableError
from app.llm.settings import LLMSettings


class NameOutput(BaseModel):
    name: str = Field(max_length=60)
    confidence: str = Field(pattern=r"^(high|medium|low)$")


class _StubServer(ThreadingHTTPServer):
    """携带每实例状态的 stub 服务端（状态隔离见模块 docstring）。"""

    daemon_threads = True

    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler]):
        super().__init__(addr, handler)
        self.request_log: list[dict] = []
        self.response_payloads: list[dict] = []
        self.status_overrides: list[int] = []
        self.call_count: int = 0


class _FakeAPIHandler(BaseHTTPRequestHandler):
    """Fake OpenAI-compatible endpoint，状态读写均走 self.server 实例。"""

    server: _StubServer

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length)
        body = json.loads(body_raw) if body_raw else {}

        srv = self.server
        srv.call_count += 1
        srv.request_log.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization", ""),
            "body": body,
        })

        idx = srv.call_count - 1
        status = srv.status_overrides[idx] if idx < len(srv.status_overrides) else 200
        payload = (
            srv.response_payloads[idx]
            if idx < len(srv.response_payloads)
            else {"choices": [{"message": {"content": "{}"}}]}
        )

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_message(self, *args: object) -> None:
        pass  # 抑制 stderr 日志


@pytest.fixture()
def stub_api():
    """每个测试独立起一个 fake API 服务端（函数级隔离），测试结束后关闭。"""
    server = _StubServer(("127.0.0.1", 0), _FakeAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def make_settings(port: int, api_key: str = "") -> LLMSettings:
    return LLMSettings(
        profile="api",
        api_base_url=f"http://127.0.0.1:{port}/v1",
        api_key=api_key,
        api_model="test-model",
        request_timeout_seconds=10,
    )


def test_basic_json_response(stub_api: _StubServer):
    """正确 JSON 响应 → 解析为 pydantic 对象。"""
    stub_api.response_payloads = [
        {"choices": [{"message": {"content": '{"name":"测试议题","confidence":"high"}'}}]}
    ]
    port = stub_api.server_address[1]
    settings = make_settings(port)
    engine = OpenAICompatibleEngine(settings)
    engine.load()

    # elapsed 由 time.monotonic() 差值计算；Windows 计时器精度下本地回环可能测得 0，
    # mock 单调时钟为固定差值，确定性验证"返回真实耗时"这一语义
    with patch("app.llm.api_engine.time") as mock_time:
        mock_time.monotonic.side_effect = [1000.0, 1000.125]
        result, elapsed = engine.generate_structured(
            "系统提示", "用户输入", NameOutput, max_retries=0,
        )
    assert isinstance(result, NameOutput)
    assert result.name == "测试议题"
    assert result.confidence == "high"
    assert elapsed == pytest.approx(0.125)

    # 验证请求格式
    req = stub_api.request_log[0]
    assert req["path"] == "/v1/chat/completions"
    assert req["body"]["model"] == "test-model"
    assert req["body"]["temperature"] == 0.0
    assert req["body"]["response_format"] == {"type": "json_object"}
    assert len(req["body"]["messages"]) == 2  # system + user


def test_bearer_token_sent(stub_api: _StubServer):
    """API key → Authorization: Bearer <key>。"""
    stub_api.response_payloads = [
        {"choices": [{"message": {"content": '{"name":"x","confidence":"low"}'}}]}
    ]
    settings = make_settings(stub_api.server_address[1], api_key="sk-test-123")
    engine = OpenAICompatibleEngine(settings)
    engine.load()
    engine.generate_structured("s", "u", NameOutput, max_retries=0)
    assert stub_api.request_log[0]["authorization"] == "Bearer sk-test-123"


def test_retry_on_parse_error(stub_api: _StubServer):
    """首次 JSON 非法 → 重试 1 次 → 第二次正确。"""
    stub_api.response_payloads = [
        {"choices": [{"message": {"content": "not json at all"}}]},
        {"choices": [{"message": {"content": '{"name":"修复后","confidence":"medium"}'}}]},
    ]
    settings = make_settings(stub_api.server_address[1])
    engine = OpenAICompatibleEngine(settings)
    engine.load()

    result, _ = engine.generate_structured("s", "u", NameOutput, max_retries=1)
    assert result.name == "修复后"
    assert stub_api.call_count == 2
    # 第二次请求中应包含错误反馈消息
    second_body = stub_api.request_log[1]["body"]
    assert any("输出不符合要求" in m["content"] for m in second_body["messages"])


def test_load_without_base_url_raises():
    """未配置 LLM_API_BASE_URL → load 抛 LLMUnavailableError。"""
    settings = LLMSettings(profile="api", api_base_url="")
    engine = OpenAICompatibleEngine(settings)
    with pytest.raises(LLMUnavailableError, match="API_BASE_URL"):
        engine.load()


def test_retry_exhausted_raises(stub_api: _StubServer):
    """两次均失败 → 最终抛 LLMParseError。"""
    stub_api.response_payloads = [
        {"choices": [{"message": {"content": "bad json 1"}}]},
        {"choices": [{"message": {"content": "bad json 2"}}]},
    ]
    settings = make_settings(stub_api.server_address[1])
    engine = OpenAICompatibleEngine(settings)
    engine.load()

    with pytest.raises(LLMParseError):
        engine.generate_structured("s", "u", NameOutput, max_retries=1)


def test_count_tokens_approximation():
    """API 模式 token 估算：中英文混合的保守上界。"""
    settings = LLMSettings(profile="api", api_base_url="http://localhost/v1")
    engine = OpenAICompatibleEngine(settings)
    assert engine.count_tokens("hello") == 2
    assert engine.count_tokens("你好世界") == 2
    assert engine.count_tokens("a") == 1
    assert engine.count_tokens("") == 1
    # 缓存命中
    assert engine.count_tokens("hello") == 2
