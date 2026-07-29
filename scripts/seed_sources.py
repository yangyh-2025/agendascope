"""种子数据导入（T1.21）：初始管理员 + 系统规则 + 17 个真实媒体源 + GDELT 兜底伪源。

用法：
    cd backend && python ../scripts/seed_sources.py
    （或在仓库根：python scripts/seed_sources.py；数据库 URL 经 .env/环境变量注入）

种子源 URL 均经实测可达（2026-07-24，feedparser 可解析）；
IIS 15 媒体任务参数（Apache-2.0 事实性配置：源 URL、抓取方式）为本清单主要参照。
脚本幂等：按 name 去重，已存在则更新 feed_url/crawl_config 等字段。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.db.session import get_session_factory, init_engine  # noqa: E402
from app.models.source import Source  # noqa: E402
from app.services.seed_service import (  # noqa: E402
    ensure_admin,
    ensure_gdelt_pseudo_source,
    ensure_system_rules,
)
from app.services.setup_service import (  # noqa: E402
    KEY_MONITOR_SCOPE,
    apply_monitor_scope,
    get_state,
)

logger = get_logger("seed")

# (name, name_zh, country, homepage, feed_url, collect_mode, adapter_type, crawl_config,
#  media_type, language, poll_interval_min, audience_weight, coverage_confidence)
SEED_SOURCES = [
    ("BBC World", "英国广播公司国际新闻", "GB", "https://www.bbc.com/news",
     "https://feeds.bbci.co.uk/news/world/rss.xml", "rss", "rss", None,
     "broadcast", "en", 5, 18.0, "high"),
    ("Voice of America", "美国之音", "US", "https://www.voanews.com",
     "https://www.voanews.com/api/zqboml-vomx-tpeivmy", "rss", "rss", None,
     "broadcast", "en", 5, 15.0, "high"),
    ("TASS", "塔斯社", "RU", "https://tass.com",
     "https://tass.com/rss/v2.xml", "rss", "rss", None,
     "agency", "en", 5, 14.0, "high"),
    ("NHK", "日本放送协会", "JP", "https://www3.nhk.or.jp",
     "https://www3.nhk.or.jp/rss/news/cat0.xml", "rss", "rss", None,
     "broadcast", "ja", 5, 16.0, "high"),
    ("Deutsche Welle", "德国之声", "DE", "https://www.dw.com",
     "https://rss.dw.com/rdf/rss-en-all", "rss", "rss", None,
     "broadcast", "en", 15, 12.0, "high"),
    ("El País", "国家报", "ES", "https://elpais.com",
     "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada", "rss", "rss", None,
     "newspaper", "es", 15, 13.0, "high"),
    ("France 24", "法兰西24", "FR", "https://www.france24.com/en",
     "https://www.france24.com/en/rss", "rss", "rss",
     {"fetcher": {"type": "playwright"}},   # 正文页反爬（403 via requests），Playwright 实测可渲染
     "broadcast", "en", 15, 11.0, "high"),
    ("RFI", "法国国际广播电台", "FR", "https://www.rfi.fr/en",
     "https://www.rfi.fr/en/rss", "rss", "rss",
     {"fetcher": {"type": "playwright"}},
     "broadcast", "en", 15, 9.0, "medium"),
    ("Anadolu Agency", "阿纳多卢通讯社", "TR", "https://www.aa.com.tr",
     "https://www.aa.com.tr/tr/rss/default?cat=guncel", "rss", "rss", None,
     "agency", "tr", 15, 12.0, "medium"),
    ("NTV", "土耳其NTV电视台", "TR", "https://www.ntv.com.tr",
     "https://www.ntv.com.tr/son-dakika.rss", "rss", "rss", None,
     "broadcast", "tr", 15, 10.0, "medium"),
    ("Yonhap News", "韩国联合通讯社", "KR", "https://www.yna.co.kr",
     "https://www.yna.co.kr/rss/news.xml", "rss", "rss", None,
     "agency", "ko", 5, 15.0, "high"),
    ("新华网", "新华网", "CN", "http://www.xinhuanet.com",
     "http://www.xinhuanet.com/politics/news_politics.xml", "rss", "rss", None,
     "agency", "zh-CN", 5, 18.0, "high"),
    ("中新网", "中国新闻网", "CN", "https://www.chinanews.com.cn",
     "https://www.chinanews.com.cn/rss/scroll-news.xml", "rss", "rss", None,
     "agency", "zh-CN", 5, 14.0, "high"),
    ("Al Jazeera", "半岛电视台", "QA", "https://www.aljazeera.com",
     "https://www.aljazeera.com/xml/rss/all.xml", "rss", "rss", None,
     "broadcast", "en", 15, 12.0, "high"),
    ("CBC News", "加拿大广播公司", "CA", "https://www.cbc.ca/news",
     "https://www.cbc.ca/webfeed/rss/rss-topstories", "rss", "rss", None,
     "broadcast", "en", 15, 13.0, "high"),
    ("ABC News (AU)", "澳大利亚广播公司", "AU", "https://www.abc.net.au/news",
     "https://www.abc.net.au/news/feed/51120/rss.xml", "rss", "rss", None,
     "broadcast", "en", 15, 13.0, "high"),
    ("Investing.com", "英为财情", "US", "https://www.investing.com",
     "https://www.investing.com/rss/news.rss", "rss", "rss",
     {"fetcher": {"type": "playwright"}},
     "online", "en", 15, 8.0, "medium"),
    # ---- 30 国扩展（G20 + 全球南方；feed URL 均经代理实测 200 且含 RSS 结构，2026-07-24） ----
    ("Times of India", "印度时报", "IN", "https://timesofindia.indiatimes.com",
     "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "rss", "rss", None,
     "newspaper", "en", 15, 15.0, "high"),
    ("The Hindu", "印度教徒报", "IN", "https://www.thehindu.com",
     "https://www.thehindu.com/news/national/feeder/default.rss", "rss", "rss", None,
     "newspaper", "en", 15, 12.0, "high"),
    ("Antara News", "安塔拉通讯社", "ID", "https://en.antaranews.com",
     "https://en.antaranews.com/rss/news.xml", "rss", "rss", None,
     "agency", "en", 15, 12.0, "medium"),
    ("G1 Globo", "环球网G1", "BR", "https://g1.globo.com",
     "https://g1.globo.com/rss/g1/", "rss", "rss", None,
     "online", "pt", 15, 16.0, "high"),
    ("Folha de S.Paulo", "圣保罗页报", "BR", "https://www.folha.uol.com.br",
     "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml", "rss", "rss", None,
     "newspaper", "pt", 15, 11.0, "medium"),
    ("La Jornada", "墨西哥日报", "MX", "https://www.jornada.com.mx",
     "https://www.jornada.com.mx/rss/edicion.xml?v=1", "rss", "rss", None,
     "newspaper", "es", 15, 11.0, "medium"),
    ("Clarín", "号角报", "AR", "https://www.clarin.com",
     "https://www.clarin.com/rss/lo-ultimo/", "rss", "rss", None,
     "newspaper", "es", 15, 13.0, "medium"),
    ("Al Riyadh", "利雅得报", "SA", "https://www.alriyadh.com",
     "https://www.alriyadh.com/rss/section/main", "rss", "rss", None,
     "newspaper", "ar", 15, 12.0, "medium"),
    ("eNCA", "南非eNCA电视台", "ZA", "https://www.enca.com",
     "https://www.enca.com/rss.xml", "rss", "rss", None,
     "broadcast", "en", 15, 11.0, "medium"),
    ("ANSA", "安莎通讯社", "IT", "https://www.ansa.it",
     "https://www.ansa.it/sito/notizie/topnews/topnews_rss.xml", "rss", "rss", None,
     "agency", "it", 15, 13.0, "high"),
    ("Corriere della Sera", "晚邮报", "IT", "https://www.corriere.it",
     "https://xml2.corriereobjects.it/rss/homepage.xml", "rss", "rss", None,
     "newspaper", "it", 15, 12.0, "high"),
    ("Daily News Egypt", "埃及每日新闻", "EG", "https://www.dailynewsegypt.com",
     "https://www.dailynewsegypt.com/feed/", "rss", "rss", None,
     "newspaper", "en", 15, 8.0, "medium"),
    ("Vanguard", "先锋报", "NG", "https://www.vanguardngr.com",
     "https://www.vanguardngr.com/feed/", "rss", "rss", None,
     "newspaper", "en", 15, 10.0, "medium"),
    ("The Standard", "标准报", "KE", "https://www.standardmedia.co.ke",
     "https://www.standardmedia.co.ke/rss/headlines.php", "rss", "rss", None,
     "newspaper", "en", 15, 10.0, "medium"),
    ("Bangkok Post", "曼谷邮报", "TH", "https://www.bangkokpost.com",
     "https://www.bangkokpost.com/rss/data/topstories.xml", "rss", "rss", None,
     "newspaper", "en", 15, 9.0, "medium"),
    ("VnExpress", "越南快讯", "VN", "https://vnexpress.net",
     "https://vnexpress.net/rss/tin-moi-nhat.rss", "rss", "rss", None,
     "online", "vi", 15, 13.0, "high"),
    ("The Daily Star", "每日星报", "BD", "https://www.thedailystar.net",
     "https://www.thedailystar.net/frontpage/rss.xml", "rss", "rss", None,
     "newspaper", "en", 15, 9.0, "medium"),
    ("Fana Broadcasting", "法纳广播公司", "ET", "https://www.fanabc.com",
     "https://www.fanabc.com/feed/", "rss", "rss", None,
     "broadcast", "en", 15, 9.0, "medium"),
    ("Tehran Times", "德黑兰时报", "IR", "https://www.tehrantimes.com",
     "https://www.tehrantimes.com/rss", "rss", "rss", None,
     "newspaper", "en", 15, 8.0, "medium"),
    ("Dawn", "黎明报", "PK", "https://www.dawn.com",
     "https://www.dawn.com/feed", "rss", "rss", None,
     "newspaper", "en", 15, 11.0, "medium"),
    ("The National", "国民报", "AE", "https://www.thenationalnews.com",
     "https://www.thenationalnews.com/arc/outboundfeeds/rss/?outputType=xml", "rss", "rss", None,
     "newspaper", "en", 15, 8.0, "medium"),
    # 无 RSS 长尾示例：adapter_type='pipeline'，ListPageDiscoverer 签名聚类免手写 selector
    ("新华网时政列表页", "新华网时政频道(列表页)", "CN", "https://www.news.cn",
     None, "rss", "pipeline",
     {"fetcher": {"type": "requests"}, "discoverer": {"type": "list_page"},
      "extractor": {"type": "trafilatura"}, "entry_points": ["https://www.news.cn/politics/"],
      "scroll_pages": 0, "post_extra_action": None, "proxy": None},
     "agency", "zh-CN", 15, 6.0, "medium"),
]


def main() -> None:
    configure_logging(debug=False)
    init_engine()
    db = get_session_factory()()
    try:
        admin = ensure_admin(db)
        ensure_system_rules(db, admin)
        ensure_gdelt_pseudo_source(db)

        created = updated = 0
        for (name, name_zh, country, homepage, feed_url, collect_mode, adapter_type,
             crawl_config, media_type, language, poll_interval, weight, confidence) in SEED_SOURCES:
            source = db.scalar(select(Source).where(Source.name == name))
            fields = {
                "name_zh": name_zh, "country_code": country, "homepage_url": homepage,
                "feed_url": feed_url, "collect_mode": collect_mode, "adapter_type": adapter_type,
                "crawl_config": crawl_config or {}, "media_type": media_type, "language": language,
                "poll_interval_min": poll_interval, "audience_weight": weight,
                "coverage_confidence": confidence,
            }
            if source is None:
                db.add(Source(name=name, **fields))
                created += 1
            else:
                for key, value in fields.items():
                    setattr(source, key, value)
                updated += 1
        db.commit()
        # 监控范围生效（T5.6）：安装向导已保存勾选国家时，导入完成后再次应用，
        # 使未勾选国家的源置 disabled（重新导入/补种不丢失范围设定）
        scope = get_state(db, KEY_MONITOR_SCOPE) or {}
        if scope.get("countries"):
            scope_result = apply_monitor_scope(db, scope["countries"])
            db.commit()
            logger.info("monitor_scope_applied", **scope_result)
        logger.info("seed_done", created=created, updated=updated,
                    admin=admin.username, total=len(SEED_SOURCES))
        print(f"种子导入完成: 新增 {created} / 更新 {updated} / 共 {len(SEED_SOURCES)} 个媒体源；管理员 {admin.username}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
