import { useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import ScrollReveal from "../components/ScrollReveal";
import { HOT_TOPICS, type HotTopic } from "../mock/demos";

export default function HotTopicsSection() {
  const [selected, setSelected] = useState<HotTopic>(HOT_TOPICS[0]);

  const chartOption = useMemo<EChartsOption>(() => {
    const sorted = [...HOT_TOPICS].sort((a, b) => a.rank - b.rank).reverse();
    return {
      backgroundColor: "transparent",
      grid: { left: 8, right: 80, top: 8, bottom: 8, containLabel: true },
      xAxis: {
        type: "value",
        show: false,
        max: 500,
      },
      yAxis: {
        type: "category",
        data: sorted.map((t) => t.name),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: "#1f2d3d",
          fontSize: 13,
          fontWeight: 500,
          margin: 12,
        },
      },
      series: [
        {
          type: "bar",
          data: sorted.map((t) => ({
            value: t.articleCount24h,
            itemStyle: {
              color:
                t.rank === selected.rank
                  ? {
                      type: "linear" as const,
                      x: 0, y: 0, x2: 1, y2: 0,
                      colorStops: [
                        { offset: 0, color: "#1a4fa0" },
                        { offset: 1, color: "#6b7fff" },
                      ],
                    }
                  : "rgba(79,127,255,0.25)",
              borderRadius: [0, 6, 6, 0],
            },
          })),
          barWidth: 18,
          label: {
            show: true,
            position: "right",
            color: "#5e6d82",
            fontSize: 12,
            fontWeight: 600,
            formatter: (p: { dataIndex: number }) => {
              const t = sorted[p.dataIndex];
              return `${t.articleCount24h} 篇 · ${t.countries} 国`;
            },
          },
          emphasis: {
            itemStyle: {
              color: {
                type: "linear" as const,
                x: 0, y: 0, x2: 1, y2: 0,
                colorStops: [
                  { offset: 0, color: "#1a4fa0" },
                  { offset: 1, color: "#6b7fff" },
                ],
              },
            },
          },
        },
      ],
      tooltip: { show: false },
    };
  }, [selected.rank]);

  return (
    <section className="lp-section lp-section-alt" id="hot-topics">
      <div className="lp-container">
        <ScrollReveal>
          <div className="lp-section-eyebrow">热点议题排行</div>
          <h2 className="lp-section-title">
            24 小时报道量
            <br />
            显著性 TOP 10
          </h2>
          <p className="lp-section-lede">
            按 24h 报道量降序聚合,全球/按国双视图,
            <br />
            点击任意议题下钻查看跨国传播链路与时间线。
          </p>
        </ScrollReveal>

        <div className="lp-hot-grid">
          {/* 左:TOP 10 横向柱状图 */}
          <ScrollReveal delay={0}>
            <div className="lp-hot-chart-wrap">
              <div className="lp-hot-chart-header">
                <span className="lp-hot-chart-title">今日 TOP 10</span>
                <span className="lp-hot-chart-hint">点击议题查看详情</span>
              </div>
              <ReactECharts
                option={chartOption}
                style={{ width: "100%", height: "440px" }}
                notMerge
                lazyUpdate
                onEvents={{
                  click: (params: { dataIndex?: number }) => {
                    if (typeof params.dataIndex === "number") {
                      const sorted = [...HOT_TOPICS].sort((a, b) => a.rank - b.rank).reverse();
                      const t = sorted[params.dataIndex];
                      if (t) setSelected(t);
                    }
                  },
                }}
              />
            </div>
          </ScrollReveal>

          {/* 右:选中议题详情卡 */}
          <ScrollReveal delay={120}>
            <div className="lp-hot-detail">
              <div className="lp-hot-detail-rank">#{selected.rank}</div>
              <h3 className="lp-hot-detail-name">{selected.name}</h3>
              <div className="lp-hot-detail-cat">{selected.category}</div>
              <div className="lp-hot-detail-stats">
                <div className="lp-hot-stat">
                  <div className="lp-hot-stat-value">{selected.articleCount24h}</div>
                  <div className="lp-hot-stat-label">24h 报道量</div>
                </div>
                <div className="lp-hot-stat">
                  <div className="lp-hot-stat-value">{selected.countries}</div>
                  <div className="lp-hot-stat-label">覆盖国家</div>
                </div>
                <div className="lp-hot-stat">
                  <div className="lp-hot-stat-value">
                    {selected.trend === "up" ? "↗" : selected.trend === "down" ? "↘" : "→"}
                  </div>
                  <div className="lp-hot-stat-label">趋势</div>
                </div>
              </div>
              <div className="lp-hot-salience">
                <div className="lp-hot-salience-header">
                  <span>显著性得分</span>
                  <span className="lp-hot-salience-value">{selected.salience}</span>
                </div>
                <div className="lp-hot-salience-bar">
                  <div
                    className="lp-hot-salience-fill"
                    style={{ width: `${selected.salience}%` }}
                  />
                </div>
              </div>
              <div className="lp-hot-detail-cta">
                <span className="lp-hot-detail-cta-text">
                  登录系统后可下钻查看完整传播链路 →
                </span>
              </div>
            </div>
          </ScrollReveal>
        </div>
      </div>
    </section>
  );
}
