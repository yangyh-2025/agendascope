"""CollectedData 提交器：POST /internal/collect（内部 token 鉴权）。

防重②：提交失败（网络/5xx）的载荷进入内存缓存，下一轮采集时优先重发（对齐 IIS 设计）。
"""
import requests

from app.collector.types import CollectedData
from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger("submitter")

_RETRYABLE_HTTP = {429, 500, 502, 503, 504}


class Submitter:
    def __init__(self, api_base: str | None = None, token: str | None = None, timeout: int = 30):
        settings = get_settings()
        self.endpoint = f"{(api_base or settings.collect_api_base).rstrip('/')}/internal/collect"
        self.token = token or settings.collector_internal_token
        self.timeout = timeout
        self._resend_buffer: list[dict] = []  # 防重②：提交失败缓存，下轮重发

    @property
    def pending_count(self) -> int:
        return len(self._resend_buffer)

    def _post(self, payload: dict) -> bool:
        try:
            resp = requests.post(
                self.endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.warning("collect_submit_network_fail", url=payload.get("url"), error=str(exc))
            return False
        if resp.status_code == 200:
            body = resp.json()
            if body.get("data", {}).get("duplicate"):
                logger.info("collect_submit_duplicate", url=payload.get("url"))
            return True
        if resp.status_code in _RETRYABLE_HTTP:
            logger.warning("collect_submit_retryable", url=payload.get("url"), status=resp.status_code)
            return False
        # 4xx（参数/鉴权问题）重发无意义，丢弃并记错误——避免缓存无限膨胀
        logger.error("collect_submit_rejected", url=payload.get("url"), status=resp.status_code, body=resp.text[:200])
        return True  # 视为已处理（不重发）

    def submit(self, data: CollectedData) -> bool:
        payload = data.to_payload()
        if self._post(payload):
            return True
        self._resend_buffer.append(payload)
        return False

    def resend_pending(self) -> int:
        """下轮采集开始时重发上轮失败缓存；返回重发成功数。"""
        if not self._resend_buffer:
            return 0
        remaining = []
        sent = 0
        for payload in self._resend_buffer:
            if self._post(payload):
                sent += 1
            else:
                remaining.append(payload)
        self._resend_buffer = remaining
        if sent:
            logger.info("collect_resend_done", resent=sent, remaining=len(remaining))
        return sent
