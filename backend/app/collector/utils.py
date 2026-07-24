"""采集工具：URL 规范化与 SHA-256 去重指纹。"""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 常见跟踪参数，规范化时剔除（避免同一文章因 utm 不同被当作两篇）
_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|spm|ref_|from=)", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """规范化 URL：小写 scheme/host、去锚点、去跟踪参数、参数排序、去默认端口。"""
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "http").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return url.strip()
    port = parsed.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not _TRACKING_PARAMS.match(k + "="))
    )
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", query, ""))


def url_hash(url: str) -> str:
    """SHA-256(规范化 url)，articles.url_hash 精确去重键。"""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()
