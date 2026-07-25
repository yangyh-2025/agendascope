/** 全球议程地图页（T4.6）：ECharts 世界地图 + 国家下钻抽屉。 */
import { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import { mapApi } from "../api/map";
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

export default function MapPage() {
  const [data, setData] = useState<MapData | null>(null);
  const [selected, setSelected] = useState<CountryItem | null>(null);

  useEffect(() => {
    mapApi.getCountries().then(setData).catch(console.error);
  }, []);

  if (!data) return <div className="loading">加载中...</div>;

  const countryMap: Record<string, string> = {
    CN: "China", US: "United States", GB: "United Kingdom", JP: "Japan",
    DE: "Germany", FR: "France", KR: "South Korea", IN: "India",
    RU: "Russia", BR: "Brazil", CA: "Canada", AU: "Australia",
    IT: "Italy", ES: "Spain", TR: "Turkey", SA: "Saudi Arabia",
    AE: "United Arab Emirates", ID: "Indonesia", ZA: "South Africa",
    NG: "Nigeria", EG: "Egypt", MX: "Mexico", AR: "Argentina",
    PL: "Poland", SE: "Sweden", NO: "Norway", CH: "Switzerland",
    NL: "Netherlands", BE: "Belgium", VN: "Vietnam",
  };

  const seriesData = data.items
    .filter((c) => c.article_count_today > 0 || c.coverage_confidence >= 0.3)
    .map((c) => ({
      name: countryMap[c.country_code] || c.country_code,
      value: Math.max(c.article_count_today || 1, 1),
      country_code: c.country_code,
      country_name_zh: c.country_name_zh,
      degraded: c.degraded,
    }));

  const option = {
    backgroundColor: "#0B2A5B",
    tooltip: {
      trigger: "item",
      formatter: (params: any) => {
        if (!params.data?.country_code) return params.name;
        const item = data.items.find((c) => c.country_code === params.data.country_code);
        if (!item) return params.name;
        return `<b>${item.country_name_zh}</b><br/>报道量: ${item.article_count_today}${item.degraded ? "<br/>⚠ 数据覆盖不足" : ""}`;
      },
    },
    visualMap: {
      min: 0,
      max: Math.max(...seriesData.map((d) => d.value), 10),
      inRange: { color: ["#1D4E9E", "#3B82F6", "#60A5FA"] },
      text: ["高", "低"],
      show: false,
    },
    geo: {
      map: "world",
      roam: true,
      itemStyle: { areaColor: "#1a3350", borderColor: "#2a4a70" },
      emphasis: { itemStyle: { areaColor: "#1D4E9E" } },
      regions: seriesData.map((d) => ({
        name: d.name,
        itemStyle: { areaColor: d.degraded ? "#374151" : undefined },
      })),
    },
    series: [
      {
        type: "scatter",
        coordinateSystem: "geo",
        data: seriesData.map((d) => [d.name, d.value]),
        symbolSize: (val: number) => Math.max(Math.sqrt(val) * 3, 8),
        itemStyle: { color: "#3B82F6", shadowBlur: 6, shadowColor: "#1D4E9E" },
        encode: { value: 2 },
      },
    ],
  };

  return (
    <div className="map-page">
      <div className="map-header">
        <h1>全球议程地图</h1>
        <span className="data-delay">数据延迟 {data.data_delay_minutes} 分钟</span>
      </div>
      <div className="map-container">
        <ReactECharts
          option={option}
          style={{ height: "70vh", width: "100%" }}
          onEvents={{ click: (params: any) => {
            const item = data.items.find((c) => countryMap[c.country_code] === params.name);
            if (item) setSelected(item);
          }}}
        />
      </div>
      {selected && (
        <div className="country-drawer">
          <h2>{selected.country_name_zh}</h2>
          <span className="country-articles">今日报道 {selected.article_count_today} 篇</span>
          {selected.degraded && <span className="degraded-tag">数据覆盖不足</span>}
          <h3>Top 议题</h3>
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
