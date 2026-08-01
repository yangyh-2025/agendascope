"""Fetcher 层（复刻 IIS 三段式设计之第一段）：Requests / Playwright。

- RequestsFetcher：静态页面抓取，随机 UA、国内外代理分级分流（global_site_proxy / cn_site_proxy）
- PlaywrightFetcher：反爬站点渲染抓取——stealth 初始化脚本、随机 UA、scroll_pages 滚动加载、
  post_extra_action 声明式点弹窗（参考 IIS NHK 案例）。浏览器二进制为运行时可选依赖。
- 付费墙/反爬应对（参考 bypass-paywalls-chrome-clean 思路，不搬运代码）：
  ① route 拦截付费脚本域名（piano.io/poool/cxense/sophi.io/ampproject 等）→ 解墙正文即现
  ② 可选 bot UA 伪装（googlebot/facebookbot，法德媒体尤多解墙）
  ③ 强墙源兜底 Google Cache 快照（正文拿不到时仍可读标题语境）
"""
import random
from typing import Any, cast
from urllib.parse import urlparse

import requests

from app.collector.types import FetchError
from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger("fetcher")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# 部分法/德媒体改 UA 为搜索引擎爬虫即返回全文（bypass-paywalls 实证信号）
BOT_USER_AGENTS = [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; facebookbot/2.2; +http://www.facebook.com/externalhit_uatext.php)",
]

# 付费墙/弹窗脚本域名：拦截后页面正文即现（bypass-paywalls 维护清单的常见信号）
_BLOCKED_SCRIPT_DOMAINS = [
    "piano.io", "poool.fr", "cxense.com", "qiota.com", "sophi.io",
    "ampproject.org", "gravito.net", "moneypenny.io", "tinypass.com",
    "permutive.com", "blueconic.net", "enlivenproject.com", "tidiochat.com",
]

# 极简 stealth：隐藏 webdriver 痕迹（复刻 IIS 反爬栈的思路，不搬运代码）
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
window.chrome = window.chrome || {runtime: {}};
"""


def _is_blocked_script(url: str) -> bool:
    """付费脚本域名判定（请求 route 拦截用）。"""
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in _BLOCKED_SCRIPT_DOMAINS)


def _google_cache_url(target: str) -> str:
    """付费墙强源正文兜底：Google Cache 快照 URL。"""
    return f"https://webcache.googleusercontent.com/search?q=cache:{target}"


def resolve_proxy(proxy_key: str | None, country_code: str = "") -> dict | None:
    """代理分级分流：配置显式指定优先；未指定时按国家码分流（CN 源走 cn_site_proxy，其余走 global）。"""
    settings = get_settings()
    key = proxy_key
    if not key:
        key = "cn_site_proxy" if country_code == "CN" else "global_site_proxy"
    proxy_url = settings.cn_site_proxy if key == "cn_site_proxy" else settings.global_site_proxy
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


class RequestsFetcher:
    type = "requests"

    def __init__(self, proxy_key: str | None = None, country_code: str = "", timeout: int | None = None,
                 verify: bool = True):
        self.settings = get_settings()
        self.proxies = resolve_proxy(proxy_key, country_code)
        self.timeout = timeout or self.settings.crawl_timeout_seconds
        self.verify = verify
        self.session = requests.Session()

    def fetch(self, url: str) -> tuple[str, int]:
        """返回 (html/text, http_status)。HTTP 错误抛 FetchError。"""
        headers = {"User-Agent": random.choice(USER_AGENTS), "Accept-Language": "en-US,en;q=0.9"}
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout, proxies=self.proxies,
                                    allow_redirects=True, verify=self.verify)
        except requests.RequestException as exc:
            raise FetchError(f"请求失败: {exc}") from exc
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code}", http_status=resp.status_code)
        resp.encoding = resp.apparent_encoding or resp.encoding
        return resp.text, resp.status_code


class PlaywrightFetcher:
    type = "playwright"

    def __init__(self, proxy_key: str | None = None, country_code: str = "",
                 scroll_pages: int = 0, post_extra_action: dict | None = None, timeout: int | None = None,
                 bot_ua: bool = False, block_paywall_scripts: bool = True):
        self.settings = get_settings()
        self.proxy_key = proxy_key
        self.country_code = country_code
        self.scroll_pages = scroll_pages or 0
        self.post_extra_action = post_extra_action
        self.timeout = (timeout or self.settings.crawl_timeout_seconds) * 1000
        self.bot_ua = bot_ua
        self.block_paywall_scripts = block_paywall_scripts

    def fetch(self, url: str) -> tuple[str, int]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError("playwright 未安装，无法渲染抓取") from exc

        proxies = resolve_proxy(self.proxy_key, self.country_code)
        proxy_conf = None
        if proxies:
            proxy_conf = {"server": proxies["https"]}
        ua = random.choice(BOT_USER_AGENTS) if self.bot_ua else random.choice(USER_AGENTS)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, proxy=cast(Any, proxy_conf))
                try:
                    context = browser.new_context(user_agent=ua)
                    context.add_init_script(_STEALTH_INIT_SCRIPT)
                    # 付费脚本域名拦截：正文页渲染不加载墙脚本（bypass-paywalls 思路）
                    if self.block_paywall_scripts:
                        context.route(
                            "**/*", lambda route: (
                                route.abort() if _is_blocked_script(route.request.url) else route.continue_()
                            )
                        )
                    page = context.new_page()
                    response = page.goto(url, timeout=self.timeout, wait_until="domcontentloaded")
                    status = response.status if response else 0
                    if status >= 400:
                        raise FetchError(f"HTTP {status}", http_status=status)
                    self._run_post_extra_action(page)
                    for _ in range(self.scroll_pages):
                        page.mouse.wheel(0, 3000)
                        page.wait_for_timeout(1000)
                    return page.content(), status
                finally:
                    browser.close()
        except FetchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise FetchError(f"Playwright 渲染失败: {exc}") from exc

    def _run_post_extra_action(self, page) -> None:
        """声明式点弹窗：{click_selector: '...'} 或 {click_text: '確認しました'}（参考 IIS NHK 案例）。"""
        action = self.post_extra_action or {}
        try:
            if action.get("click_selector"):
                page.click(action["click_selector"], timeout=5000)
                page.wait_for_timeout(1000)
            elif action.get("click_text"):
                page.get_by_text(action["click_text"], exact=False).first.click(timeout=5000)
                page.wait_for_timeout(1000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("post_extra_action_failed", action=action, error=str(exc))


def build_fetcher(config: dict, country_code: str = ""):
    cfg = (config or {}).get("fetcher") or {"type": "requests"}
    proxy_key = (config or {}).get("proxy")
    insecure_ssl = bool((config or {}).get("insecure_ssl"))
    if cfg.get("type") == "playwright":
        return PlaywrightFetcher(
            proxy_key=proxy_key,
            country_code=country_code,
            scroll_pages=(config or {}).get("scroll_pages", 0),
            post_extra_action=(config or {}).get("post_extra_action"),
            timeout=cfg.get("timeout"),
            bot_ua=bool((config or {}).get("bot_ua")),
            block_paywall_scripts=bool((config or {}).get("block_paywall_scripts", True)),
        )
    return RequestsFetcher(
        proxy_key=proxy_key, country_code=country_code,
        timeout=cfg.get("timeout"), verify=not insecure_ssl,
    )
