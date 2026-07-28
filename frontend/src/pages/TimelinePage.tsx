/** 议程时间线页（T4.8）：议题维度堆叠面积图 + 粒度切换 + 峰值打标 + 代表头条。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { ApiError } from "../api/client";
import {
  getTopicTimeline,
  listTopicArticles,
  listTopics,
  type TimelinePoint,
  type TopicArticleItem,
  type TopicListItem,
} from "../api/topics";
import { effectiveGranularity, type RequestedGranularity, WEEK_DOWNGRADE_DAYS } from "../utils/timeline";
import "./TimelinePage.css";

const MAX_TOPICS = 5;
const DAY_OPTIONS = [7, 30, 90, 180];
const SERIES_COLORS = ["#3B82F6", "#2FA96B", "#D9A02B", "#8B5CF6", "#38BDF8"];

interface TopicSeries {
  topic: TopicListItem;
  points: TimelinePoint[];
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function TimelinePage() {
  const [topics, setTopics] = useState<TopicListItem[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [requestedGranularity, setRequestedGranularity] = useState<RequestedGranularity>("day");
  const [days, setDays] = useState(7);
  const [series, setSeries] = useState<TopicSeries[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [headlines, setHeadlines] = useState<{ topicName: string; items: TopicArticleItem[] } | null>(null);

  // 候选议题（按显著性）
  useEffect(() => {
    listTopics({ sort: "salience", page: 1, page_size: 30 })
      .then((r) => {
        setTopics(r.items);
        setSelectedIds((prev) => (prev.length > 0 ? prev : r.items.slice(0, 2).map((t) => t.id)));
      })
      .catch((err) => setError(errMsg(err, "议题列表加载失败")));
  }, []);

  const granularity = effectiveGranularity(days, requestedGranularity);

  const load = useCallback(() => {
    if (selectedIds.length === 0) {
      setSeries([]);
      return;
    }
    setError(null);
    Promise.all(
      selectedIds.map(async (id) => {
        const topic = topics.find((t) => t.id === id);
        const tl = await getTopicTimeline(id, { days, granularity });
        return { topic: topic ?? ({ id, name: id } as TopicListItem), points: tl.points };
      }),
    )
      .then(setSeries)
      .catch((err) => setError(errMsg(err, "时间线数据加载失败")));
  }, [selectedIds, days, granularity, topics]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleTopic = (id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].slice(-MAX_TOPICS),
    );
  };

  const option = useMemo(() => {
    // 以并集时间轴对齐各议题序列
    const axis = [...new Set(series.flatMap((s) => s.points.map((p) => p.window_start)))].sort();
    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        formatter: (params: { seriesName: string; value: number; axisValue: string }[]) => {
          const head = params[0]?.axisValue?.replace("T", " ").slice(0, 16) ?? "";
          const lines = params.map((p) => `${p.seriesName}：${p.value} 篇`);
          return [head, ...lines, "点击峰值查看 3 条代表头条"].join("<br/>");
        },
      },
      legend: { textStyle: { color: "#9DB2D0" }, top: 0 },
      grid: { left: 48, right: 24, top: 40, bottom: 32 },
      xAxis: {
        type: "category",
        data: axis.map((t) => t.slice(5, 16).replace("T", " ")),
        axisLabel: { color: "#9DB2D0" },
      },
      yAxis: {
        type: "value",
        name: "报道量",
        nameTextStyle: { color: "#9DB2D0" },
        axisLabel: { color: "#9DB2D0" },
        splitLine: { lineStyle: { color: "#1e3a5f" } },
      },
      series: series.map((s, idx) => {
        const byTime = new Map(s.points.map((p) => [p.window_start, p.article_count]));
        const values = axis.map((t) => byTime.get(t) ?? 0);
        return {
          name: s.topic.name,
          type: "line",
          stack: "articles",
          smooth: true,
          areaStyle: { opacity: 0.3 },
          itemStyle: { color: SERIES_COLORS[idx % SERIES_COLORS.length] },
          emphasis: { focus: "series" },
          data: values,
          // 峰值自动打标
          markPoint: {
            symbol: "pin",
            symbolSize: 42,
            label: { color: "#0B2A5B", fontSize: 10, formatter: "峰" },
            itemStyle: { color: SERIES_COLORS[idx % SERIES_COLORS.length] },
            data: [{ type: "max", name: "峰值" }],
          },
        };
      }),
    };
  }, [series]);

  const onChartClick = useCallback(
    (params: { seriesIndex?: number }) => {
      const s = typeof params.seriesIndex === "number" ? series[params.seriesIndex] : undefined;
      if (!s) return;
      listTopicArticles(s.topic.id, { page: 1, page_size: 3 })
        .then((r) => setHeadlines({ topicName: s.topic.name, items: r.items }))
        .catch(() => setHeadlines({ topicName: s.topic.name, items: [] }));
    },
    [series],
  );

  return (
    <div className="timeline-page">
      <h1>议程时间线</h1>
      <div className="timeline-controls">
        <div className="control-group">
          <span className="control-label">粒度</span>
          <button
            className={`gran-btn ${requestedGranularity === "hour" ? "active" : ""}`}
            onClick={() => setRequestedGranularity("hour")}
          >
            1h
          </button>
          <button
            className={`gran-btn ${requestedGranularity === "day" ? "active" : ""}`}
            onClick={() => setRequestedGranularity("day")}
          >
            1d
          </button>
        </div>
        <div className="control-group">
          <span className="control-label">时间窗</span>
          {DAY_OPTIONS.map((d) => (
            <button key={d} className={`gran-btn ${days === d ? "active" : ""}`} onClick={() => setDays(d)}>
              {d} 天
            </button>
          ))}
        </div>
        {days > WEEK_DOWNGRADE_DAYS && (
          <span className="gran-note">时间窗超过 {WEEK_DOWNGRADE_DAYS} 天，已自动降为周粒度</span>
        )}
      </div>
      <div className="topic-picker">
        {topics.map((t) => (
          <button
            key={t.id}
            className={`topic-chip ${selectedIds.includes(t.id) ? "active" : ""}`}
            onClick={() => toggleTopic(t.id)}
            title={t.name}
          >
            {t.name.length > 18 ? `${t.name.slice(0, 18)}…` : t.name}
          </button>
        ))}
      </div>

      {error && <p className="page-error" role="alert">{error}</p>}
      {!error && selectedIds.length === 0 && <p className="page-loading">请选择至少一个议题（最多 {MAX_TOPICS} 个）</p>}

      {selectedIds.length > 0 && (
        <div className="chart-panel">
          <ReactECharts
            option={option}
            style={{ height: "56vh", width: "100%" }}
            onEvents={{ click: onChartClick }}
          />
        </div>
      )}

      {headlines && (
        <div className="headlines-panel">
          <div className="headlines-head">
            <h2>代表头条 · {headlines.topicName}</h2>
            <button className="as-btn-ghost" onClick={() => setHeadlines(null)}>关闭</button>
          </div>
          {headlines.items.length === 0 && <p className="drawer-empty">该议题暂无文章</p>}
          <ul>
            {headlines.items.map((a) => (
              <li key={a.id}>
                <a href={a.url} target="_blank" rel="noreferrer">{a.title}</a>
                <span>{a.source_name ?? "未知来源"} · {a.published_at?.slice(0, 16).replace("T", " ") ?? ""}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
