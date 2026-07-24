"""正文抽取降级链单元测试（trafilatura → readability → 标题摘要）。"""
from app.collector.extractor import (
    METHOD_READABILITY,
    METHOD_TITLE_SUMMARY,
    METHOD_TRAFILATURA,
    extract_content,
    extract_with_fallback,
)

ARTICLE_HTML = """
<html><head><title>Test</title></head><body>
<nav>菜单链接 首页 关于我们 联系方式</nav>
<article>
<h1>某国宣布新的经济刺激计划</h1>
<p>该国政府今日宣布了一项总额超过五千亿元的经济刺激计划，涵盖基础设施建设、
小微企业扶持与居民消费补贴三大方向。财政部表示，资金将在未来十八个月内分批到位，
重点投向中西部地区的交通与能源项目。</p>
<p>多位经济学家认为，此举将有助于稳定就业市场，但也可能加剧地方政府债务压力。
央行随后回应称将保持流动性合理充裕，配合财政政策形成合力。</p>
<p>反对党则批评该计划缺乏透明度，要求议会召开特别会议审议具体条款。
市场反应总体积极，主要股指当日收涨百分之一点二。</p>
</article>
<footer>版权所有 不得转载</footer>
</body></html>
"""

THIN_HTML = "<html><body><div><a href='/x'>link</a><a href='/y'>more</a></div></body></html>"


class TestExtractContent:
    def test_trafilatura_full(self):
        result = extract_content(ARTICLE_HTML, "https://example.com/news/1")
        assert result.ok
        assert result.method == METHOD_TRAFILATURA
        assert result.content_status == "full"
        assert "经济刺激计划" in result.text

    def test_empty_html_failed(self):
        result = extract_content("", "https://example.com/x")
        assert result.content_status == "failed"

    def test_thin_page_falls_back_or_fails(self):
        result = extract_content(THIN_HTML, "https://example.com/x")
        assert result.method in (METHOD_READABILITY, "none")


class TestExtractWithFallback:
    def test_title_summary_partial(self):
        result = extract_with_fallback("", "https://example.com/x", "这是一个足够长度的新闻标题内容", "")
        assert result.content_status == "partial"
        assert result.method == METHOD_TITLE_SUMMARY

    def test_full_when_html_good(self):
        result = extract_with_fallback(ARTICLE_HTML, "https://example.com/news/1", "标题", "")
        assert result.content_status == "full"
