"""ApiEmbedder 集成测试：内嵌 stub HTTP 服务端验证 /embeddings 协议。

验证 ApiEmbedder：
- 正确发送 POST /embeddings 含 model/input
- 正确解析 data[i].embedding，按 index 排序
- 维度从首次响应自动识别并缓存
- L2 归一化（与本地 Embedder normalize_embeddings=True 对齐）
- 鉴权头 Bearer token 正确传递
- 未配置 base_url 抛 EmbeddingUnavailableError
- 401/5xx 抛 EmbeddingUnavailableError

隔离性同 test_llm_api_engine：ThreadingHTTPServer + 函数级 fixture，状态挂 server 实例。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.nlp.api_embedder import ApiEmbedder, EmbeddingUnavailableError
from app.nlp.config import NlpSettings


class _StubServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler]):
        super().__init__(addr, handler)
        self.request_log: list[dict] = []
        self.response_payloads: list[dict] = []
        self.status_overrides: list[int] = []
        self.call_count: int = 0


class _FakeAPIHandler(BaseHTTPRequestHandler):
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
            else {"data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]}
        )

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_message(self, *args: object) -> None:
        pass  # 抑制 stderr 日志


@pytest.fixture()
def stub_api():
    server = _StubServer(("127.0.0.1", 0), _FakeAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def make_settings(port: int, api_key: str = "") -> NlpSettings:
    return NlpSettings(
        embedding_profile="api",
        embedding_api_base_url=f"http://127.0.0.1:{port}/v1",
        embedding_api_key=api_key,
        embedding_api_model="test-embed",
    )


def _vec(dim: int, seed: int) -> list[float]:
    """确定性伪向量：各分量同号保证非零。"""
    return [float((seed + i) % 5 + 1) / 5.0 for i in range(dim)]


def test_basic_embed_and_dim_identify(stub_api: _StubServer):
    """正确响应 → 返回向量，维度自动识别并缓存。"""
    stub_api.response_payloads = [
        {"data": [
            {"embedding": _vec(4, 1), "index": 0},
            {"embedding": _vec(4, 2), "index": 1},
        ]}
    ]
    port = stub_api.server_address[1]
    embedder = ApiEmbedder(make_settings(port))
    embedder.load()

    result = embedder.embed(["第一段文本", "第二段文本"])
    assert len(result) == 2
    assert len(result[0]) == 4
    assert embedder.dim == 4  # 首次响应识别并缓存

    # 请求格式
    req = stub_api.request_log[0]
    assert req["path"] == "/v1/embeddings"
    assert req["body"]["model"] == "test-embed"
    assert req["body"]["input"] == ["第一段文本", "第二段文本"]


def test_l2_normalization(stub_api: _StubServer):
    """输出向量 L2 归一化（模长=1），余弦检索可直接用。"""
    stub_api.response_payloads = [
        {"data": [{"embedding": [3.0, 4.0], "index": 0}]}
    ]
    port = stub_api.server_address[1]
    embedder = ApiEmbedder(make_settings(port))
    embedder.load()

    vec = embedder.embed(["文本"])[0]
    norm = sum(v * v for v in vec) ** 0.5
    assert norm == pytest.approx(1.0)
    assert vec[0] == pytest.approx(0.6)  # 3/5
    assert vec[1] == pytest.approx(0.8)  # 4/5


def test_embed_index_ordering(stub_api: _StubServer):
    """API 乱序返回 data 时按 index 排序，保证与输入顺序一致。"""
    stub_api.response_payloads = [
        {"data": [
            {"embedding": _vec(3, 99), "index": 1},
            {"embedding": _vec(3, 1), "index": 0},
        ]}
    ]
    port = stub_api.server_address[1]
    embedder = ApiEmbedder(make_settings(port))
    embedder.load()

    result = embedder.embed(["a", "b"])
    # _vec(3,1) = [2,3,4]/5 → 归一化首分量 ≈ 0.371；_vec(3,99) = [5,1,2]/5 → 归一化首分量 ≈ 0.913。
    # 关键语义：index=0 的向量（分量更小）排在前，与输入顺序 a->b 一致。
    assert result[0][0] < result[1][0]
    assert result[0][0] == pytest.approx(0.4 / (0.4**2 + 0.6**2 + 0.8**2) ** 0.5)
    assert result[1][0] == pytest.approx(1.0 / (1.0**2 + 0.2**2 + 0.4**2) ** 0.5)


def test_bearer_token_sent(stub_api: _StubServer):
    """API key → Authorization: Bearer <key>。"""
    stub_api.response_payloads = [
        {"data": [{"embedding": _vec(2, 1), "index": 0}]}
    ]
    port = stub_api.server_address[1]
    embedder = ApiEmbedder(make_settings(port, api_key="sk-embed-123"))
    embedder.load()
    embedder.embed(["x"])
    assert stub_api.request_log[0]["authorization"] == "Bearer sk-embed-123"


def test_load_without_base_url_raises():
    """未配置 API_BASE_URL → load 抛 EmbeddingUnavailableError。"""
    settings = NlpSettings(embedding_profile="api", embedding_api_base_url="")
    embedder = ApiEmbedder(settings)
    with pytest.raises(EmbeddingUnavailableError, match="API_BASE_URL"):
        embedder.load()


def test_unauthorized_raises(stub_api: _StubServer):
    """401 → 抛 EmbeddingUnavailableError 带鉴权信息。"""
    stub_api.status_overrides = [401]
    port = stub_api.server_address[1]
    embedder = ApiEmbedder(make_settings(port))
    embedder.load()
    with pytest.raises(EmbeddingUnavailableError, match="鉴权"):
        embedder.embed(["x"])


def test_server_error_raises(stub_api: _StubServer):
    """5xx → 抛 EmbeddingUnavailableError。"""
    stub_api.status_overrides = [503]
    port = stub_api.server_address[1]
    embedder = ApiEmbedder(make_settings(port))
    embedder.load()
    with pytest.raises(EmbeddingUnavailableError, match="服务端"):
        embedder.embed(["x"])


def test_empty_input_returns_empty():
    """空输入 → 空列表（不触发网络请求）。"""
    embedder = ApiEmbedder(NlpSettings(embedding_profile="api"))
    assert embedder.embed([]) == []
