import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import ScrollReveal from "../components/ScrollReveal";
import { COLLECTION_FEED } from "../mock/demos";
import { COUNTRIES } from "../mock/countries";
import { registerWorldMap, countryCenter } from "../../../map/worldMap";

// 模块加载即注册世界地图(避免 useEffect 顺序问题导致 ECharts setOption 时地图未注册)
registerWorldMap();

export default function CollectionSection() {
  // 轮播 feed:每 2s 把第一条移到最后,形成无限滚动
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => {
      setOffset((v) => (v + 1) % COLLECTION_FEED.length);
    }, 2200);
    return () => clearInterval(timer);
  }, []);

  const visibleFeed = useMemo(() => {
    const items: typeof COLLECTION_FEED = [];
    for (let i = 0; i < 8; i++) {
      items.push(COLLECTION_FEED[(offset + i) % COLLECTION_FEED.length]);
    }
    return items;
  }, [offset]);

  // 世界地图:108 国光点 + 当前活跃点(取 feed 第一条国家)高亮
  const mapOption = useMemo<EChartsOption>(() => {
    const activeCountry = COLLECTION_FEED[offset]?.countryCode;
    const coveragePoints = COUNTRIES.map((c) => {
      const center = countryCenter(c.code);
      return center
        ? {
            name: c.nameZh,
            value: [...center, 0] as [number, number, number],
            itemStyle: {
              color: c.code === activeCountry ? "#c8102e" : "rgba(26, 79, 160, 0.4)",
            },
            symbolSize: c.code === activeCountry ? 14 : 5,
          }
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
          borderColor: "rgba(26, 79, 160, 0.25)",
          borderWidth: 0.5,
        },
        emphasis: { disabled: true },
        select: { disabled: true },
      },
      series: [
        {
          type: "scatter",
          coordinateSystem: "geo",
          data: coveragePoints,
          itemStyle: {
            shadowBlur: 8,
            shadowColor: "rgba(26, 79, 160, 0.3)",
          },
          zlevel: 2,
        },
        // 当前活跃点涟漪
        ...(activeCountry && countryCenter(activeCountry)
          ? [
              {
                type: "effectScatter" as const,
                coordinateSystem: "geo" as const,
                data: [
                  {
                    name: activeCountry,
                    value: [...(countryCenter(activeCountry) as [number, number]), 0],
                  },
                ],
                symbolSize: 14,
                rippleEffect: { brushType: "stroke" as const, scale: 4, period: 2 },
                itemStyle: {
                  color: "#c8102e",
                  shadowBlur: 12,
                  shadowColor: "#c8102e",
                },
                zlevel: 3,
              },
            ]
          : []),
      ],
    };
  }, [offset]);

  return (
    <section className="lp-section" id="collection">
      <div className="lp-container">
        <ScrollReveal>
          <div className="lp-section-eyebrow">实时采集引擎</div>
          <h2 className="lp-section-title">
            408 个主流源,
            <br />
            P95 ≤ 30 分钟发布到可见
          </h2>
          <p className="lp-section-lede">
            重点源 RSS 高频轮询 + GDELT 全球兜底,慢源超时降级,吞吐稳定。
            <br />
            每一篇报道从发布到进入分析管线,全程可观测。
          </p>
        </ScrollReveal>

        <div className="lp-collect-grid">
          {/* 左:世界地图 + 108 国光点 + 当前活跃点涟漪 */}
          <ScrollReveal delay={0}>
            <div className="lp-collect-map-wrap">
              <div className="lp-collect-map-header">
                <span className="lp-collect-pulse" aria-hidden="true" />
                <span className="lp-collect-live-tag">LIVE · 172 国监控中</span>
              </div>
              <ReactECharts
                option={mapOption}
                style={{ width: "100%", height: "100%" }}
                notMerge
                lazyUpdate
              />
            </div>
          </ScrollReveal>

          {/* 右:滚动采集 feed */}
          <ScrollReveal delay={120}>
            <div className="lp-collect-feed">
              <div className="lp-collect-feed-header">
                <span className="lp-collect-feed-title">实时采集流</span>
                <span className="lp-collect-feed-rate">~ 33 篇 / 5min</span>
              </div>
              <div className="lp-collect-feed-list">
                {visibleFeed.map((item, i) => (
                  <div
                    key={`${item.countryCode}-${item.headline}-${i}`}
                    className={`lp-collect-item ${i === 0 ? "lp-collect-item-latest" : ""}`}
                    style={{ opacity: 1 - i * 0.12 }}
                  >
                    <div className="lp-collect-item-meta">
                      <span className="lp-collect-item-flag">{item.countryCode}</span>
                      <span className="lp-collect-item-outlet">{item.outlet}</span>
                      <span className="lp-collect-item-time">{item.time}</span>
                    </div>
                    <div className="lp-collect-item-headline">{item.headline}</div>
                  </div>
                ))}
              </div>
            </div>
          </ScrollReveal>
        </div>
      </div>
    </section>
  );
}
