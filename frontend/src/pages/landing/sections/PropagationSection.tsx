import { useMemo, useRef } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import ScrollReveal from "../components/ScrollReveal";
import { PROPAGATION_DEMO } from "../mock/stats";
import { COUNTRIES } from "../mock/countries";
import { registerWorldMap, countryCenter } from "../../../map/worldMap";

// 模块加载即注册世界地图(避免 useEffect 顺序问题导致 ECharts setOption 时地图未注册)
registerWorldMap();

/** 演示议题的传播地图:ECharts 世界地图 + 飞线 + 涟漪散点。 */
export default function PropagationSection() {
  const chartRef = useRef<ReactECharts>(null);

  const option = useMemo<EChartsOption>(() => {
    const origin = PROPAGATION_DEMO.find((n) => n.role === "origin");
    const followers = PROPAGATION_DEMO.filter((n) => n.role === "follower");
    if (!origin) return {};

    const originCoord = countryCenter(origin.countryCode);
    if (!originCoord) return {};

    // 飞线数据(从首发源到各跟随国)
    const lines = followers
      .map((f) => {
        const to = countryCenter(f.countryCode);
        if (!to) return null;
        return {
          coords: [originCoord, to],
          value: f.lagHours,
        };
      })
      .filter((l): l is NonNullable<typeof l> => Boolean(l));

    // 散点(首发源 + 跟随国)
    const originPoint = [
      {
        name: origin.countryName,
        value: [...originCoord, 0] as [number, number, number],
        itemStyle: { color: "#c8102e" },
      },
    ];
    const followerPoints = followers
      .map((f) => {
        const c = countryCenter(f.countryCode);
        if (!c) return null;
        return {
          name: f.countryName,
          value: [...c, f.lagHours] as [number, number, number],
          itemStyle: { color: "#1a4fa0" },
        };
      })
      .filter((p): p is NonNullable<typeof p> => Boolean(p));

    // 108 国覆盖点(微小灰点,展示监控范围)
    const coveragePoints = COUNTRIES.map((c) => {
      const center = countryCenter(c.code);
      return center
        ? { name: c.nameZh, value: [...center, 0] as [number, number, number] }
        : null;
    }).filter((p): p is NonNullable<typeof p> => Boolean(p));

    return {
      backgroundColor: "transparent",
      geo: {
        map: "world",
        roam: false,
        silent: true,
        zoom: 1.2,
        center: [30, 20] as [number, number],
        itemStyle: {
          areaColor: "#e8f0fb",
          borderColor: "rgba(26, 79, 160, 0.3)",
          borderWidth: 0.5,
        },
        emphasis: { disabled: true },
        select: { disabled: true },
      },
      series: [
        // 108 国覆盖点(背景)
        {
          type: "scatter",
          coordinateSystem: "geo",
          data: coveragePoints,
          symbolSize: 3,
          itemStyle: { color: "rgba(26, 79, 160, 0.35)" },
          silent: true,
          zlevel: 1,
        },
        // 飞线
        {
          type: "lines",
          coordinateSystem: "geo",
          polyline: false,
          effect: {
            show: true,
            period: 4,
            trailLength: 0.4,
            symbol: "arrow",
            symbolSize: 6,
            color: "#c8102e",
          },
          lineStyle: {
            color: "#c8102e",
            width: 1.4,
            opacity: 0.55,
            curveness: 0.3,
          },
          data: lines,
          zlevel: 2,
        },
        // 跟随国涟漪
        {
          type: "effectScatter",
          coordinateSystem: "geo",
          data: followerPoints,
          symbolSize: 10,
          rippleEffect: { brushType: "stroke", scale: 3.5, period: 3 },
          itemStyle: { color: "#1a4fa0", shadowBlur: 8, shadowColor: "#1a4fa0" },
          label: {
            show: true,
            position: "right",
            formatter: (p: { name?: string }) => p.name ?? "",
            color: "#1f2d3d",
            fontSize: 11,
            fontWeight: 500,
            textBorderColor: "rgba(255,255,255,0.9)",
            textBorderWidth: 3,
          },
          zlevel: 3,
        },
        // 首发源(大涟漪)
        {
          type: "effectScatter",
          coordinateSystem: "geo",
          data: originPoint,
          symbolSize: 16,
          rippleEffect: { brushType: "stroke", scale: 5, period: 2.5 },
          itemStyle: {
            color: "#c8102e",
            shadowBlur: 16,
            shadowColor: "#c8102e",
          },
          label: {
            show: true,
            position: "right",
            formatter: `{b} · 首发源`,
            color: "#c8102e",
            fontSize: 13,
            fontWeight: 700,
            textBorderColor: "rgba(255,255,255,0.95)",
            textBorderWidth: 3,
          },
          zlevel: 4,
        },
      ],
    };
  }, []);

  return (
    <section className="lp-section lp-section-alt" id="propagation">
      <div className="lp-container">
        <ScrollReveal>
          <div className="lp-section-eyebrow">议程溯源</div>
          <h2 className="lp-section-title">
            识别首发源,
            <br />
            还原跨国传播链路
          </h2>
          <p className="lp-section-lede">
            回声消除折叠多国跟风报道,按 lag_hours 升序排布跟随国序列。
            <br />
            每一条链路都可下钻、可质疑、可修正,全程留痕。
          </p>
        </ScrollReveal>

        <ScrollReveal delay={120}>
          <div className="lp-prop-demo">
            <div className="lp-prop-header">
              <span className="lp-prop-eyebrow">演示案例</span>
              <span className="lp-prop-topic">
                美国提出新经济框架 → 6 国陆续跟随报道
              </span>
            </div>
            <div className="lp-prop-map-wrap">
              <ReactECharts
                ref={chartRef}
                option={option}
                style={{ width: "100%", height: "100%" }}
                notMerge
                lazyUpdate
              />
            </div>

            {/* 时间轴(纯文字,补充地图外的时序信息) */}
            <div className="lp-prop-timeline">
              {PROPAGATION_DEMO.map((node) => (
                <div
                  key={node.countryCode}
                  className={`lp-prop-timeline-item ${
                    node.role === "origin" ? "lp-prop-timeline-origin" : ""
                  }`}
                >
                  <div className="lp-prop-timeline-lag">
                    {node.role === "origin" ? "T+0" : `T+${node.lagHours.toFixed(1)}h`}
                  </div>
                  <div className="lp-prop-timeline-country">{node.countryName}</div>
                  <div className="lp-prop-timeline-outlet">{node.outlet}</div>
                </div>
              ))}
            </div>
          </div>
        </ScrollReveal>

        <ScrollReveal delay={200}>
          <div className="lp-prop-footnote">
            <span className="lp-prop-footnote-icon" aria-hidden="true">ⓘ</span>
            演示数据虚构,仅展示能力。生产环境数据基于实时采集与回放验证。
          </div>
        </ScrollReveal>
      </div>
    </section>
  );
}
