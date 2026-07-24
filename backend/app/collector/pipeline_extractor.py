"""Extractor 层（三段式之第三段）：GenericCSS 定制抽取 + trafilatura→readability 公共降级链。

crawl_config.extractor:
  {"type": "trafilatura"|"readability"|"generic_css",
   "content_css": "article .body", "content_xpath": "//article", "title_css": "h1"}
"""
import lxml.html

from app.collector.extractor import (
    METHOD_TRAFILATURA,
    ExtractResult,
    _clean,
    extract_content,
    extract_with_fallback,
)
from app.core.logging import get_logger

logger = get_logger("pipeline.extractor")

METHOD_GENERIC_CSS = "generic_css"


def extract_generic_css(html: str, url: str, config: dict) -> ExtractResult:
    """按 crawl_config 中声明的 CSS/XPath selector 抽取正文（种子参数可复用 IIS 15 媒体配置）。"""
    try:
        doc = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return ExtractResult(text="", method=METHOD_GENERIC_CSS, content_status="failed")

    nodes = []
    xpath = (config or {}).get("content_xpath")
    css = (config or {}).get("content_css")
    if xpath:
        nodes = doc.xpath(xpath)
    elif css:
        try:
            from cssselect import GenericTranslator  # noqa: F401

            nodes = doc.cssselect(css)
        except Exception as exc:  # noqa: BLE001
            logger.warning("generic_css_selector_invalid", selector=css, error=str(exc))
            nodes = []
    text = _clean("\n".join(n.text_content() for n in nodes if hasattr(n, "text_content")))
    if len(text) >= 10:
        return ExtractResult(text=text, method=METHOD_GENERIC_CSS, content_status="full")
    return ExtractResult(text="", method=METHOD_GENERIC_CSS, content_status="failed")


def extract_pipeline(html: str, url: str, title: str, summary: str, extractor_config: dict) -> ExtractResult:
    """pipeline 抽取入口：优先按配置指定方式，失败回落公共降级链（不静默，method 留痕）。"""
    cfg = extractor_config or {}
    etype = cfg.get("type", METHOD_TRAFILATURA)
    if etype == METHOD_GENERIC_CSS:
        result = extract_generic_css(html, url, cfg)
        if result.ok:
            return result
    elif etype == "readability":
        result = extract_content(html, url)  # trafilatura 失败自动进 readability
        if result.ok:
            return result
    return extract_with_fallback(html, url, title, summary)
