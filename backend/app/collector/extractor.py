"""正文抽取降级链（T1.18）：trafilatura → readability-lxml → 仅存标题摘要。

content_status: full（正文抽取成功）/ partial（仅标题摘要兜底）/ failed（无任何可用文本）。
降级链是设计功能（PRD 8.5）：绝不静默降级，返回对象携带实际命中的 method。
"""
from dataclasses import dataclass, field

import trafilatura
from readability import Document

from app.core.logging import get_logger

logger = get_logger("extractor")

METHOD_TRAFILATURA = "trafilatura"
METHOD_READABILITY = "readability"
METHOD_TITLE_SUMMARY = "title_summary"
METHOD_NONE = "none"

MIN_CONTENT_LEN = 10


@dataclass
class ExtractResult:
    text: str
    method: str
    content_status: str  # full / partial / failed

    @property
    def ok(self) -> bool:
        return self.content_status == "full"


def _clean(text: str | None) -> str:
    return (text or "").strip()


def extract_content(html: str, url: str = "") -> ExtractResult:
    """对正文 HTML 执行降级链抽取。"""
    if html:
        # 第一级：trafilatura
        try:
            text = _clean(trafilatura.extract(html, url=url or None, include_comments=False, include_tables=False))
            if len(text) >= MIN_CONTENT_LEN:
                return ExtractResult(text=text, method=METHOD_TRAFILATURA, content_status="full")
        except Exception as exc:  # noqa: BLE001
            logger.warning("trafilatura_failed", url=url, error=str(exc))

        # 第二级：readability-lxml
        try:
            doc = Document(html)
            import lxml.html

            frag = lxml.html.fromstring(doc.summary())
            text = _clean(frag.text_content())
            if len(text) >= MIN_CONTENT_LEN:
                logger.warning("extract_fallback", url=url, fallback=METHOD_READABILITY, reason="trafilatura_empty")
                return ExtractResult(text=text, method=METHOD_READABILITY, content_status="full")
        except Exception as exc:  # noqa: BLE001
            logger.warning("readability_failed", url=url, error=str(exc))

    return ExtractResult(text="", method=METHOD_NONE, content_status="failed")


def extract_with_fallback(html: str, url: str, title: str, summary: str = "") -> ExtractResult:
    """完整降级链：两级正文抽取失败后，仅存标题+摘要（content_status=partial）。"""
    result = extract_content(html, url)
    if result.ok:
        return result
    fallback_text = _clean(f"{title}\n{summary}" if summary else title)
    if len(fallback_text) >= MIN_CONTENT_LEN:
        logger.warning(
            "extract_fallback", url=url, fallback=METHOD_TITLE_SUMMARY, reason=f"{result.method}_failed"
        )
        return ExtractResult(text=fallback_text, method=METHOD_TITLE_SUMMARY, content_status="partial")
    return result
