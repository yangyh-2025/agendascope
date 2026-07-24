"""SSRF 防护（详细设计 7.3）：用户提交 URL 仅允许 http/https，且禁止内网地址。"""
import ipaddress
import socket
from urllib.parse import urlparse

from app.core.errors import CODE_URL_INVALID, BizError

_ALLOWED_SCHEMES = {"http", "https"}


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_url(url: str, resolve_dns: bool = True) -> str:
    """校验 URL 合法性；非法时抛 BizError(code=1002)。返回原 URL。"""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise BizError(CODE_URL_INVALID, "URL 仅允许 http/https 协议")
    host = parsed.hostname
    if not host:
        raise BizError(CODE_URL_INVALID, "URL 缺少主机名")
    # 直接以 IP 形式给出的主机先做判断
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        ips = []
    if not ips and resolve_dns:
        try:
            infos = socket.getaddrinfo(host, None)
            ips = sorted({str(info[4][0]) for info in infos})
        except socket.gaierror:
            raise BizError(CODE_URL_INVALID, "URL 主机名无法解析") from None
    if any(_is_private_ip(ip) for ip in ips):
        raise BizError(CODE_URL_INVALID, "URL 指向内网地址，已被拦截")
    return url
