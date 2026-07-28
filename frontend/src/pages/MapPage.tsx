/** 全球议程地图页（T4.6 + T4.13）：离线世界地图 + 30 国高亮、缺源置灰 + 国家下钻抽屉。 */
import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { ApiError } from "../api/client";
import { mapApi } from "../api/map";
import DegradedBadge from "../components/DegradedBadge";
import { mapNameOf, registerWorldMap } from "../map/worldMap";
import "./MapPage.css";

interface CountryItem {
  country_code: string;
  country_name_zh: string;
  article_count_today: number;
  top_topics: { topic_id: string; name: string; salience_score: number; article_count: number }[];
  coverage_confidence: number;
  degraded: boolean;
  data_delay_minutes: number;
}

type MapData = { items: CountryItem[]; data_delay_minutes: number; coverage_confidence: number };

/** 覆盖率置信度三档（T4.13）：高 ≥0.85 / 中 0.7–0.85 / 低 <0.7。 */
export function coverageTier(confidence: number): { label: string; className: string } {
  if (confidence >= 0.85) return { label: "高", className: "coverage-tier coverage-high" };
  if (confidence >= 0.7) return { label: "中", className: "coverage-tier coverage-mid" };
  return { label: "低", className: "coverage-tier coverage-low" };
}

export default function MapPage() {
  const [data, setData] = useState<MapData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<CountryItem | null>(null);

  // 注册离线世界地图（构建期打包的 GeoJSON，运行时无网络请求）
  const geoReady = useMemo(() => {
    registerWorldMap();
    return true;
  }, []);

  useEffect(() => {
    let cancelled = false;
    mapApi
      .getCountries()
      .then((d: MapData) => {
        if (!cancelled) setData(d);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "地图数据加载失败，请稍后重试");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div className="map-page">
        <h1>全球议程地图</h1>
        <p className="page-error" role="alert">{error}</p>
      </div>
    );
  }
  if (!data || !geoReady) return <div className="loading">加载中...</div>;

  const items = data.items ?? [];
  const byCode = new Map(items.map((c) => [c.country_code, c]));

  // 有数据的 30 国进入 visualMap 蓝阶；degraded 单独置灰，不冒充旧数据
  const seriesData = items
    .filter((c) => mapNameOf(c.country_code) && c.article_count_today > 0)
    .map((c) => ({
      name: mapNameOf(c.country_code)!,
      value: c.article_count_today,
      country_code: c.country_code,
    }));

  const maxCount = Math.max(...seriesData.map((d) => d.value), 10);

  // 覆盖/降级国家区域着色：degraded → 灰；有数据 → 交给 visualMap
  const greyRegions = items
    .filter((c) => mapNameOf(c.country_code) && c.degraded)
    .map((c) => ({
      name: mapNameOf(c.country_code)!,
      itemStyle: { areaColor: "#374151" },
      emphasis: { itemStyle: { areaColor: "#4B5563" } },
    }));

  const option = {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item",
      formatter: (params: { name: string; data?: { country_code?: string } }) => {
        const code = params.data?.country_code;
        if (!code) return `${params.name}<br/>暂无数据源`;
        const item = byCode.get(code);
        if (!item) return `${params.name}<br/>暂无数据源`;
        const lines = [
          `<b>${item.country_name_zh}</b>`,
          `今日报道：${item.article_count_today} 篇`,
          `覆盖率置信度：${(item.coverage_confidence * 100).toFixed(0)}%`,
        ];
        if (item.degraded) lines.push("⚠ 数据覆盖不足（不使用旧数据冒充）");
        return lines.join("<br/>");
      },
    },
    visualMap: {
      min: 0,
      max: maxCount,
      inRange: { color: ["#16345f", "#1D4E9E", "#3B82F6", "#93C5FD"] },
      text: ["报道量高", "低"],
      left: 12,
      bottom: 12,
      calculable: true,
      textStyle: { color: "#9DB2D0" },
    },
    series: [
      {
        type: "map",
        map: "world",
        roam: true,
        // 缺源国家保持素色灰底，不进入 visualMap 数据域
        itemStyle: { areaColor: "#132743", borderColor: "#274B84", borderWidth: 0.5 },
        emphasis: {
          label: { show: false },
          itemStyle: { areaColor: "#1D4E9E" },
        },
        select: { disabled: true },
        regions: greyRegions,
        data: seriesData,
      },
    ],
  };

  const tier = coverageTier(data.coverage_confidence);

  return (
    <div className="map-page">
      <div className="map-header">
        <h1>全球议程地图</h1>
        <div className="map-header-meta">
          <span className={tier.className}>覆盖率置信度：{tier.label}（{(data.coverage_confidence * 100).toFixed(0)}%）</span>
          {tier.label === "低" && <DegradedBadge kind="coverage_low" />}
          <span className="data-delay">数据延迟 {data.data_delay_minutes} 分钟</span>
        </div>
      </div>
      <div className="map-container">
        <ReactECharts
          option={option}
          style={{ height: "70vh", width: "100%" }}
          onEvents={{
            click: (params: { name?: string }) => {
              const item = items.find((c) => mapNameOf(c.country_code) === params.name);
              if (item) setSelected(item);
            },
          }}
        />
      </div>
      {selected && (
        <div className="country-drawer">
          <h2>{selected.country_name_zh}</h2>
          <span className="country-articles">今日报道 {selected.article_count_today} 篇</span>
          <span className={coverageTier(selected.coverage_confidence).className}>
            置信度{coverageTier(selected.coverage_confidence).label}
          </span>
          {selected.degraded && <DegradedBadge kind="coverage_low" />}
          <h3>Top 议题</h3>
          {selected.top_topics.length === 0 && <p className="drawer-empty">今日暂无议题快照</p>}
          <ul>
            {selected.top_topics.map((t) => (
              <li key={t.topic_id}>
                <a href={`/topics/${t.topic_id}`}>{t.name}</a>
                <span>显著性 {t.salience_score.toFixed(2)} · {t.article_count} 篇</span>
              </li>
            ))}
          </ul>
          <button onClick={() => setSelected(null)}>关闭</button>
        </div>
      )}
    </div>
  );
}
