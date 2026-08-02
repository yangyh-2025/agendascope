/** 总览页:KPI + 世界地图 + 健康度 TOP10 + 国家下钻抽屉。 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import ReactECharts from "echarts-for-react";
import { ApiError } from "../api/client";
import { listSources, type SourceListItem } from "../api/sources";
import { mapApi } from "../api/map";
import { listHotTopics, type HotTopicItem } from "../api/topics";
import { countryLabel } from "../api/meta";
import DegradedBadge from "../components/DegradedBadge";
import { mapNameOf, registerWorldMap } from "../map/worldMap";
import "./OverviewPage.css";

interface CountryItem {
  country_code: string;
  country_name_zh: string;
  article_count_today: number;
  top_topics: { topic_id: string; name: string; salience_score: number; article_count: number }[];
  coverage_confidence: number;
  degraded: boolean;
  data_delay_minutes: number;
}

type MapData = {
  items: CountryItem[];
  data_delay_minutes: number;
  coverage_confidence: number;
};

const PAGE_SIZE = 100;

async function fetchAllSources(): Promise<SourceListItem[]> {
  const first = await listSources({ page: 1, page_size: PAGE_SIZE });
  const items = [...first.items];
  const pages = Math.ceil(first.total / PAGE_SIZE);
  for (let page = 2; page <= pages; page += 1) {
    const res = await listSources({ page, page_size: PAGE_SIZE });
    items.push(...res.items);
  }
  return items;
}

/** 数字滚动动画。 */
function useCountUp(target: number, duration = 600): number {
  const [value, setValue] = useState(0);
  const rafRef = useRef<number | null>(null);
  useEffect(() => {
    if (target === 0) {
      setValue(0);
      return;
    }
    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(Math.round(target * eased));
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [target, duration]);
  return value;
}

function KpiCard({
  label,
  value,
  suffix,
  tone,
}: {
  label: string;
  value: number;
  suffix?: string;
  tone: "primary" | "success" | "warning" | "accent";
}) {
  const animated = useCountUp(value);
  return (
    <div className={`kpi-card kpi-${tone}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">
        {animated}
        {suffix && <span className="kpi-suffix">{suffix}</span>}
      </div>
    </div>
  );
}

function coverageTier(confidence: number): { label: string; className: string } {
  if (confidence >= 0.85) return { label: "高", className: "coverage-tier coverage-high" };
  if (confidence >= 0.7) return { label: "中", className: "coverage-tier coverage-mid" };
  return { label: "低", className: "coverage-tier coverage-low" };
}

export default function OverviewPage() {
  const navigate = useNavigate();
  const [sources, setSources] = useState<SourceListItem[] | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [mapData, setMapData] = useState<MapData | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [selected, setSelected] = useState<CountryItem | null>(null);
  const [hotTopics, setHotTopics] = useState<HotTopicItem[] | null>(null);
  const [hotError, setHotError] = useState<string | null>(null);

  const geoReady = useMemo(() => {
    registerWorldMap();
    return true;
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchAllSources()
      .then((items) => {
        if (!cancelled) setSources(items);
      })
      .catch((err) => {
        if (!cancelled) {
          setSourcesError(err instanceof ApiError ? err.message : "媒体源数据加载失败");
        }
      });
    mapApi
      .getCountries()
      .then((d: MapData) => {
        if (!cancelled) setMapData(d);
      })
      .catch((err) => {
        if (!cancelled) {
          setMapError(err instanceof ApiError ? err.message : "地图数据加载失败");
        }
      });
    listHotTopics(10)
      .then((d) => {
        if (!cancelled) setHotTopics(d.items);
      })
      .catch((err) => {
        if (!cancelled) {
          setHotError(err instanceof ApiError ? err.message : "热点议题加载失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /* ========== KPI 计算 ========== */
  const kpis = useMemo(() => {
    if (!sources) return { countries: 0, total: 0, active: 0, failed: 0 };
    const countrySet = new Set<string>();
    let active = 0;
    let failed = 0;
    for (const s of sources) {
      countrySet.add(s.country_code);
      if (s.status === "active") active += 1;
      else if (s.status === "failed") failed += 1;
    }
    return {
      countries: countrySet.size,
      total: sources.length,
      active,
      failed,
    };
  }, [sources]);

  /* ========== 世界地图 ECharts ========== */
  const mapOption = useMemo(() => {
    if (!mapData) return null;
    const items = mapData.items ?? [];
    const byCode = new Map(items.map((c) => [c.country_code, c]));

    const seriesData = items
      .filter((c) => mapNameOf(c.country_code) && c.article_count_today > 0)
      .map((c) => ({
        name: mapNameOf(c.country_code)!,
        value: c.article_count_today,
        country_code: c.country_code,
      }));

    const maxCount = Math.max(...seriesData.map((d) => d.value), 10);

    const greyRegions = items
      .filter((c) => mapNameOf(c.country_code) && c.degraded)
      .map((c) => ({
        name: mapNameOf(c.country_code)!,
        itemStyle: { areaColor: "#D1D9E6" },
        emphasis: { itemStyle: { areaColor: "#B6C2D6" } },
      }));

    return {
      backgroundColor: "transparent",
      animationDuration: 600,
      animationEasing: "cubicOut",
      tooltip: {
        trigger: "item",
        backgroundColor: "#FFFFFF",
        borderColor: "#E4E9F2",
        borderWidth: 1,
        padding: [10, 14],
        textStyle: { color: "#1F2D3D", fontSize: 13 },
        extraCssText: "box-shadow: 0 4px 16px rgba(15, 61, 138, 0.12); border-radius: 8px;",
        formatter: (params: { name: string; data?: { country_code?: string } }) => {
          const code = params.data?.country_code;
          if (!code) return `<b>${params.name}</b><br/><span style="color:#9AA8BB">暂无数据源</span>`;
          const item = byCode.get(code);
          if (!item) return `<b>${params.name}</b><br/><span style="color:#9AA8BB">暂无数据源</span>`;
          const lines = [
            `<b style="color:#1A4FA0">${item.country_name_zh}</b>`,
            `今日报道:<b>${item.article_count_today}</b> 篇`,
            `覆盖率置信度:<b>${(item.coverage_confidence * 100).toFixed(0)}%</b>`,
          ];
          if (item.degraded) lines.push('<span style="color:#C8102E">⚠ 数据覆盖不足</span>');
          return lines.join("<br/>");
        },
      },
      visualMap: {
        min: 0,
        max: maxCount,
        inRange: { color: ["#E8F0FB", "#A9C4EB", "#5B8CD6", "#1A4FA0", "#0F3D8A"] },
        text: ["报道量高", "低"],
        left: 16,
        bottom: 16,
        calculable: true,
        textStyle: { color: "#5E6D82", fontSize: 12 },
        itemWidth: 12,
        itemHeight: 100,
      },
      series: [
        {
          type: "map",
          map: "world",
          roam: false,
          itemStyle: {
            areaColor: "#F0F4FA",
            borderColor: "#FFFFFF",
            borderWidth: 0.6,
          },
          emphasis: {
            label: { show: false },
            itemStyle: {
              areaColor: "#2B63C4",
              shadowBlur: 12,
              shadowColor: "rgba(26, 79, 160, 0.3)",
            },
          },
          select: { disabled: true },
          regions: greyRegions,
          data: seriesData,
        },
      ],
    };
  }, [mapData]);

  const loading = !sources && !sourcesError;
  const tier = mapData ? coverageTier(mapData.coverage_confidence) : null;

  return (
    <div className="overview-page">
      <header className="page-header">
        <div>
          <h1 className="page-title">全球议程总览</h1>
          <p className="page-desc">
            媒体源覆盖、健康度与今日报道实时分布,点击地图国家查看 Top 议题。
          </p>
        </div>
        {mapData && tier && (
          <div className="overview-meta">
            <span className={tier.className}>
              覆盖率置信度:{tier.label}({(mapData.coverage_confidence * 100).toFixed(0)}%)
            </span>
            {tier.label === "低" && <DegradedBadge kind="coverage_low" />}
            <span className="data-delay">数据延迟 {mapData.data_delay_minutes} 分钟</span>
          </div>
        )}
      </header>

      {sourcesError && (
        <p className="page-error" role="alert">
          {sourcesError}
        </p>
      )}
      {mapError && (
        <p className="page-error" role="alert">
          {mapError}
        </p>
      )}
      {loading && <p className="page-loading">加载中…</p>}

      {sources && (
        <div className="kpi-grid">
          <KpiCard label="覆盖国家/地区" value={kpis.countries} suffix="个" tone="primary" />
          <KpiCard label="媒体源总数" value={kpis.total} suffix="个" tone="success" />
          <KpiCard label="正常源" value={kpis.active} suffix="个" tone="success" />
          <KpiCard label="失败/告警" value={kpis.failed} suffix="个" tone="accent" />
        </div>
      )}

      <div className="overview-main">
        <div className="overview-map-card">
          {mapOption && geoReady ? (
            <ReactECharts
              option={mapOption}
              style={{ height: "60vh", width: "100%" }}
              onEvents={{
                click: (params: { name?: string }) => {
                  const items = mapData?.items ?? [];
                  const item = items.find((c) => mapNameOf(c.country_code) === params.name);
                  if (item) setSelected(item);
                },
              }}
            />
          ) : (
            <div className="map-loading">地图加载中…</div>
          )}
        </div>

        <aside className="overview-side">
          <div className="side-card">
            <div className="side-card-head">
              <h3 className="side-card-title">热点议题 TOP 10</h3>
              <a className="side-card-more" href="/topics">查看全部 →</a>
            </div>
            {hotError && <p className="page-error">{hotError}</p>}
            {!hotTopics && !hotError && <p className="page-loading">热点议题加载中…</p>}
            {hotTopics && hotTopics.length === 0 && (
              <p className="drawer-empty">今日暂无热点议题</p>
            )}
            {hotTopics && hotTopics.length > 0 && (
              <ul className="hot-topic-list">
                {hotTopics.map((t, idx) => (
                  <li
                    key={t.id}
                    className="hot-topic-item clickable"
                    role="button"
                    tabIndex={0}
                    onClick={() => navigate(`/topics/${t.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        navigate(`/topics/${t.id}`);
                      }
                    }}
                    title={`查看议题「${t.name}」详情`}
                  >
                    <span className={`rank-no rank-${idx + 1 <= 3 ? idx + 1 : "n"}`}>{idx + 1}</span>
                    <div className="hot-topic-body">
                      <span className="hot-topic-name">{t.name}</span>
                      <span className="hot-topic-meta">
                        显著性 {t.salience_score.toFixed(2)}
                        {t.article_count > 0 ? ` · ${t.article_count} 篇` : ""}
                        {t.media_count > 0 ? ` · ${t.media_count} 源` : ""}
                        {t.salience_country ? ` · ${countryLabel(t.salience_country)}` : ""}
                        {t.has_agenda_event ? " · 议程事件" : ""}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>
      </div>

      {selected && (
        <>
          <div className="country-drawer-mask" onClick={() => setSelected(null)} />
          <aside className="country-drawer">
            <header className="country-drawer-head">
              <h2>{selected.country_name_zh}</h2>
              <button
                className="country-drawer-close"
                onClick={() => setSelected(null)}
                aria-label="关闭"
              >
                ✕
              </button>
            </header>
            <div className="country-drawer-meta">
              <div className="country-stat">
                <span className="country-stat-label">今日报道</span>
                <span className="country-stat-value">{selected.article_count_today}</span>
              </div>
              <div className="country-stat">
                <span className="country-stat-label">置信度</span>
                <span className={coverageTier(selected.coverage_confidence).className}>
                  {coverageTier(selected.coverage_confidence).label}
                </span>
              </div>
            </div>
            {selected.degraded && (
              <div className="country-drawer-alert">
                <DegradedBadge kind="coverage_low" />
              </div>
            )}
            <h3 className="country-drawer-section">Top 议题</h3>
            {selected.top_topics.length === 0 && (
              <p className="drawer-empty">今日暂无议题快照</p>
            )}
            <ul className="country-topic-list">
              {selected.top_topics.map((t) => (
                <li key={t.topic_id}>
                  <a href={`/topics/${t.topic_id}`}>{t.name}</a>
                  <span>
                    显著性 {t.salience_score.toFixed(2)} · {t.article_count} 篇
                  </span>
                </li>
              ))}
            </ul>
            <button
              className="as-btn-ghost drawer-jump-btn"
              onClick={() => navigate(`/sources?country=${selected.country_code}`)}
            >
              查看 {selected.country_name_zh} 媒体源 →
            </button>
          </aside>
        </>
      )}
    </div>
  );
}
