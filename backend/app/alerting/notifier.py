"""通知通道（T4.15）：站内 / SMTP 邮件 / Webhook（企业微信/钉钉/飞书）。

- 站内：alerts 表 unread 列表，前端轮询拉取（不在本模块写库——引擎已写 unread）
- 邮件：smtplib 直连 SMTP 内网邮件网关；失败记 notify_result.email={status:'failed', error, retry_at}，
  指数退避 1m/5m/15m 重试 3 次
- Webhook：httpx POST JSON 到企业微信/钉钉/飞书 webhook URL；
  URL 白名单校验防 SSRF（必须 https + 非内网 IP）；
  失败 3 次指数退避后降级邮件 + 标记 webhook 失效

notify_result 字段（JSONB）结构：
{
  "inapp":   {"status": "ok"},
  "email":   {"status": "ok"|"failed"|"skipped", "attempts": [...], "error": str|None},
  "webhook": {"status": "ok"|"failed"|"skipped", "attempts": [...], "fallback": "email"|None}
}
"""
from __future__ import annotations

import contextlib
import ipaddress
import json
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.header import Header
from email.mime.text import MIMEText
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.alert import Alert, AlertRule
from app.models.user import User

logger = get_logger("alerting.notifier")

# 指数退避间隔（秒）：1m / 5m / 15m
RETRY_BACKOFF_SECONDS = (60, 300, 900)

# Webhook 允许的主机白名单（企业微信/钉钉/飞书 默认域名）
WEBHOOK_ALLOWED_HOSTS = (
    "qyapi.weixin.qq.com",
    "oapi.dingtalk.com",
    "open.feishu.cn",
    "open.larksuite.com",
)


# ---------------------------------------------------------------------------
# SSRF 防护
# ---------------------------------------------------------------------------


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


def is_safe_webhook_url(url: str, allowed_hosts: tuple[str, ...] = WEBHOOK_ALLOWED_HOSTS) -> tuple[bool, str]:
    """SSRF 校验：仅 https + 主机在白名单 + DNS 解析后非内网 IP。

    返回 (is_safe, reason)；reason 供失败时填充错误消息。
    """
    if not url:
        return False, "webhook_url 为空"
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "URL 解析失败"
    if parsed.scheme != "https":
        return False, "webhook 必须 https"
    host = parsed.hostname or ""
    if not host:
        return False, "webhook 主机为空"
    if allowed_hosts and host.lower() not in allowed_hosts:
        return False, f"webhook 主机 {host} 不在白名单（{','.join(allowed_hosts)}）"
    # DNS 解析后必须是公网地址
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False, f"webhook 主机 {host} DNS 解析失败"
    for info in infos:
        ip_str: str = str(info[4][0])
        if _is_private_ip(ip_str):
            return False, f"webhook 主机 {host} 解析到内网地址 {ip_str}，已拦截"
    return True, "ok"


# ---------------------------------------------------------------------------
# 通知结果数据类
# ---------------------------------------------------------------------------


@dataclass
class ChannelAttempt:
    """单次通道发送尝试。"""

    at: str                       # ISO 时间戳
    status: str                   # ok | failed
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at, "status": self.status, "error": self.error}


@dataclass
class NotifyOutcome:
    """单条 alert 的全通道通知结果。"""

    inapp: dict[str, Any] = field(default_factory=lambda: {"status": "ok"})
    email: dict[str, Any] = field(default_factory=lambda: {"status": "skipped"})
    webhook: dict[str, Any] = field(default_factory=lambda: {"status": "skipped"})

    def to_dict(self) -> dict[str, Any]:
        return {"inapp": self.inapp, "email": self.email, "webhook": self.webhook}


# ---------------------------------------------------------------------------
# SMTP 邮件发送
# ---------------------------------------------------------------------------


class SmtpConfig:
    """SMTP 内网邮件网关配置（由 AlertingConfig 注入）。"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 25,
        from_addr: str = "alert@agendascope.local",
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = False,
        timeout_seconds: int = 10,
    ):
        self.host = host
        self.port = port
        self.from_addr = from_addr
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.timeout_seconds = timeout_seconds


def _build_email_message(
    subject: str,
    body: str,
    from_addr: str,
    to_addr: str,
) -> MIMEText:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["From"] = from_addr
    msg["To"] = to_addr
    return msg


def send_email(
    cfg: SmtpConfig,
    to_addr: str,
    subject: str,
    body: str,
    smtp_factory=None,
) -> tuple[bool, str | None]:
    """发送单封邮件。返回 (success, error)。

    smtp_factory: 测试注入，签名 (host, port, timeout) -> smtplib.SMTP-like
    """
    factory = smtp_factory or (lambda h, p, t: smtplib.SMTP(h, p, timeout=t))
    try:
        client = factory(cfg.host, cfg.port, cfg.timeout_seconds)
        try:
            if cfg.use_tls:
                ctx = ssl.create_default_context()
                client.starttls(context=ctx)
            if cfg.username and cfg.password:
                client.login(cfg.username, cfg.password)
            msg = _build_email_message(subject, body, cfg.from_addr, to_addr)
            client.sendmail(cfg.from_addr, [to_addr], msg.as_string())
        finally:
            with contextlib.suppress(Exception):
                client.quit()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


# ---------------------------------------------------------------------------
# Webhook 发送（企业微信/钉钉/飞书）
# ---------------------------------------------------------------------------


def _build_webhook_payload(url: str, title: str, text: str) -> dict[str, Any]:
    """按目标平台组装 webhook payload（基于 URL 主机名）。"""
    host = (urlparse(url).hostname or "").lower()
    if "qyapi.weixin.qq.com" in host:
        return {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    if "oapi.dingtalk.com" in host:
        return {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    if "feishu" in host or "larksuite" in host:
        return {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
    # 兜底：通用 JSON
    return {"title": title, "text": text}


def send_webhook(
    url: str,
    title: str,
    text: str,
    timeout_seconds: int = 10,
    http_client: httpx.Client | None = None,
) -> tuple[bool, str | None]:
    """POST JSON 到 webhook URL。返回 (success, error)。"""
    safe, reason = is_safe_webhook_url(url)
    if not safe:
        return False, f"SSRF 拦截: {reason}"
    payload = _build_webhook_payload(url, title, text)
    client = http_client
    own_client = False
    if client is None:
        client = httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        own_client = True
    try:
        resp = client.post(url, json=payload)
        if 200 <= resp.status_code < 300:
            return True, None
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]
    finally:
        if own_client:
            client.close()


# ---------------------------------------------------------------------------
# 文本渲染
# ---------------------------------------------------------------------------


def render_alert_text(alert: Alert, rule: AlertRule) -> tuple[str, str]:
    """从 alert.payload 渲染邮件/Webhook 标题与正文。"""
    payload = alert.payload or {}
    kind = payload.get("kind")
    if kind == "alert_storm_digest":
        title = "[AgendaScope] 预警风暴合并摘要"
        body = payload.get("message", "")
        return title, body
    if kind == "rule_triggered":
        matched = payload.get("matched") or []
        first = matched[0] if matched else {}
        title = f"[AgendaScope] 预警触发 - {payload.get('rule_name', rule.name)}"
        lines = [
            f"规则：{payload.get('rule_name', rule.name)}",
            f"触发时间：{alert.triggered_at.isoformat() if alert.triggered_at else ''}",
            f"命中议题数：{len(matched)}",
        ]
        for m in matched[:5]:
            lines.append(
                f"- [{m.get('country_code','')}] {m.get('topic_name','')} "
                f"显著性 #{m.get('salience_rank','')} 报道 {m.get('article_count',0)} 篇"
            )
        if payload.get("event_id"):
            lines.append(f"关联议程设置事件：{payload['event_id']}")
        if first:
            lines.append(f"首发国/显著性最高国：{first.get('country_code','')}")
        body = "\n".join(lines)
        return title, body
    if kind == "snapshot_refresh_failure":
        title = "[AgendaScope] 快照刷新失败 P1"
        body = payload.get("message", "")
        return title, body
    title = "[AgendaScope] 系统通知"
    body = json.dumps(payload, ensure_ascii=False)[:1000]
    return title, body


# ---------------------------------------------------------------------------
# 主入口：对单条 alert 全通道发送
# ---------------------------------------------------------------------------


def notify_alert(
    db: Session,
    alert: Alert,
    rule: AlertRule,
    smtp_config: SmtpConfig | None = None,
    smtp_factory=None,
    http_client: httpx.Client | None = None,
    retry_attempt: int = 0,
) -> dict[str, Any]:
    """对一条 alert 按 rule.notify_channels 全通道发送，写 notify_result。

    retry_attempt: 当前重试序号（0 表示首发；>0 表示由退避队列触发的重试）。
    返回 notify_result dict（也写入 alert.notify_result 并 flush）。

    注意：站内通道（inapp）仅是 alerts 表 unread 记录，无需主动发送。
    """
    outcome = NotifyOutcome()
    channels = list(rule.notify_channels or [])
    title, body = render_alert_text(alert, rule)

    # 站内：仅记录 ok（前端轮询拉取）
    if "inapp" in channels or not channels:
        outcome.inapp = {"status": "ok"}

    # 邮件
    if "email" in channels:
        user = db.get(User, alert.user_id)
        to_addr = user.email if user and user.email else None
        if not to_addr:
            outcome.email = {"status": "skipped", "reason": "user_email_missing"}
        elif smtp_config is None:
            outcome.email = {"status": "skipped", "reason": "smtp_not_configured"}
        else:
            ok_flag, err = send_email(
                smtp_config, to_addr, title, body, smtp_factory=smtp_factory,
            )
            attempts = list((alert.notify_result or {}).get("email", {}).get("attempts", []))
            attempts.append(ChannelAttempt(
                at=datetime.now(UTC).isoformat(),
                status="ok" if ok_flag else "failed",
                error=err,
            ).to_dict())
            outcome.email = {
                "status": "ok" if ok_flag else "failed",
                "error": err,
                "attempts": attempts,
                "to": to_addr,
                "next_retry_at": (
                    _next_retry_at(retry_attempt) if not ok_flag and retry_attempt < len(RETRY_BACKOFF_SECONDS) else None
                ),
            }

    # Webhook
    if "webhook" in channels and rule.webhook_url:
        ok_flag, err = send_webhook(rule.webhook_url, title, body, http_client=http_client)
        attempts = list((alert.notify_result or {}).get("webhook", {}).get("attempts", []))
        attempts.append(ChannelAttempt(
            at=datetime.now(UTC).isoformat(),
            status="ok" if ok_flag else "failed",
            error=err,
        ).to_dict())
        # 已重试 N 次仍失败 → 降级邮件 + 标记 webhook 失效
        fallback: str | None = None
        if not ok_flag and retry_attempt >= len(RETRY_BACKOFF_SECONDS) - 1:
            fallback = "email"
            # 标记规则 webhook 失效：写 rule.enabled 不动，仅在 notify_result 反映
        outcome.webhook = {
            "status": "ok" if ok_flag else "failed",
            "error": err,
            "attempts": attempts,
            "url_host": (urlparse(rule.webhook_url).hostname or ""),
            "fallback": fallback,
            "next_retry_at": (
                _next_retry_at(retry_attempt) if not ok_flag and retry_attempt < len(RETRY_BACKOFF_SECONDS) else None
            ),
        }

    result = outcome.to_dict()
    alert.notify_result = result
    db.flush()
    logger.info(
        "alert_notify_done",
        alert_id=str(alert.id),
        rule_id=str(rule.id),
        channels=channels,
        result=result,
    )
    return result


def _next_retry_at(retry_attempt: int) -> str:
    """根据当前重试序号计算下次重试时刻（ISO）。"""
    if retry_attempt >= len(RETRY_BACKOFF_SECONDS):
        return ""
    backoff = RETRY_BACKOFF_SECONDS[retry_attempt]
    return (datetime.now(UTC).timestamp() + backoff).__int__().__str__()


__all__ = [
    "RETRY_BACKOFF_SECONDS",
    "WEBHOOK_ALLOWED_HOSTS",
    "ChannelAttempt",
    "NotifyOutcome",
    "SmtpConfig",
    "is_safe_webhook_url",
    "notify_alert",
    "render_alert_text",
    "send_email",
    "send_webhook",
]
