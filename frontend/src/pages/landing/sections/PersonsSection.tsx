import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import ScrollReveal from "../components/ScrollReveal";
import { PERSON_DEMO } from "../mock/demos";

const TYPE_COLOR: Record<string, string> = {
  person: "#c8102e",
  org: "#1a4fa0",
  country: "#6b7fff",
  thinktank: "#16a34a",
};

const TYPE_LABEL: Record<string, string> = {
  person: "人物",
  org: "机构",
  country: "国家",
  thinktank: "智库",
};

export default function PersonsSection() {
  const chartOption = useMemo<EChartsOption>(() => {
    const nodes = PERSON_DEMO.nodes.map((n) => ({
      id: n.id,
      name: n.name,
      value: n.mentions,
      symbolSize: n.id === "center" ? 52 : Math.max(14, Math.sqrt(n.mentions) * 2.6),
      itemStyle: {
        color: TYPE_COLOR[n.type],
        shadowBlur: n.id === "center" ? 22 : 8,
        shadowColor: TYPE_COLOR[n.type],
        borderColor: "rgba(255,255,255,0.25)",
        borderWidth: n.id === "center" ? 2 : 1,
      },
      label: {
        show: true,
        color: "#1f2d3d",
        fontSize: n.id === "center" ? 14 : 10,
        fontWeight: n.id === "center" ? 700 : 500,
      },
      category: n.type,
    }));

    const links = PERSON_DEMO.links.map((l) => ({
      source: l.source,
      target: l.target,
      label: {
        show: false,  // 高密度网络下默认不显示关系文字,避免拥挤;hover 才显示
        formatter: l.label,
        color: "#5e6d82",
        fontSize: 9,
      },
      lineStyle: {
        color: "rgba(26, 79, 160, 0.3)",
        width: 1,
        curveness: 0.18,
      },
      emphasis: {
        label: { show: true },
        lineStyle: { width: 2, color: "rgba(26, 79, 160, 0.7)" },
      },
    }));

    return {
      backgroundColor: "transparent",
      series: [
        {
          type: "graph",
          layout: "force",
          roam: true,
          scaleLimit: { min: 0.6, max: 2.5 },
          data: nodes,
          links: links,
          force: {
            repulsion: 320,
            edgeLength: [40, 90],
            gravity: 0.12,
            friction: 0.25,
            layoutAnimation: true,
          },
          label: {
            position: "bottom",
            distance: 4,
          },
          emphasis: {
            focus: "adjacency",
            lineStyle: { width: 2 },
          },
          categories: [
            { name: "person" },
            { name: "org" },
            { name: "country" },
            { name: "thinktank" },
          ],
        },
      ],
    };
  }, []);

  return (
    <section className="lp-section" id="persons">
      <div className="lp-container">
        <ScrollReveal>
          <div className="lp-section-eyebrow">人物/机构监测</div>
          <h2 className="lp-section-title">
            跟踪关键实体
            <br />
            首发信号与关联网络
          </h2>
          <p className="lp-section-lede">
            关键人物、智库、国际组织实体库 + NER 自动登记。
            <br />
            同名歧义置信度衰减,首发信号自动入人工队列。
          </p>
        </ScrollReveal>

        <div className="lp-persons-grid">
          {/* 左:关系网络图 */}
          <ScrollReveal delay={0}>
            <div className="lp-persons-chart-wrap">
              <div className="lp-persons-chart-header">
                <span className="lp-persons-chart-title">
                  {PERSON_DEMO.centerPerson.name} · 关联网络
                </span>
                <div className="lp-persons-legend">
                  {Object.entries(TYPE_LABEL).map(([key, label]) => (
                    <span key={key} className="lp-persons-legend-item">
                      <span
                        className="lp-persons-legend-dot"
                        style={{ background: TYPE_COLOR[key] }}
                      />
                      {label}
                    </span>
                  ))}
                </div>
              </div>
              <ReactECharts
                option={chartOption}
                style={{ width: "100%", height: "520px" }}
                notMerge
                lazyUpdate
              />
            </div>
          </ScrollReveal>

          {/* 右:监测能力说明 + 数据 */}
          <ScrollReveal delay={120}>
            <div className="lp-persons-side">
              <div className="lp-persons-metric-card">
                <div className="lp-persons-metric-num">
                  {PERSON_DEMO.centerPerson.mentions}
                </div>
                <div className="lp-persons-metric-label">本周提及次数</div>
                <div className="lp-persons-metric-trend">↗ 较上周 +18%</div>
              </div>

              <div className="lp-persons-features">
                <div className="lp-persons-feature">
                  <div className="lp-persons-feature-icon">🎯</div>
                  <div>
                    <div className="lp-persons-feature-title">NER 自动登记</div>
                    <div className="lp-persons-feature-desc">
                      jieba 中文 + 英文大写规则,新实体自动入库
                    </div>
                  </div>
                </div>
                <div className="lp-persons-feature">
                  <div className="lp-persons-feature-icon">🔀</div>
                  <div>
                    <div className="lp-persons-feature-title">同名歧义处理</div>
                    <div className="lp-persons-feature-desc">
                      别名表精确匹配,歧义置信度衰减,人工队列复核
                    </div>
                  </div>
                </div>
                <div className="lp-persons-feature">
                  <div className="lp-persons-feature-icon">⚡</div>
                  <div>
                    <div className="lp-persons-feature-title">首发信号</div>
                    <div className="lp-persons-feature-desc">
                      首次表态自动识别,LLM 判定表述性质并留痕
                    </div>
                  </div>
                </div>
                <div className="lp-persons-feature">
                  <div className="lp-persons-feature-icon">📊</div>
                  <div>
                    <div className="lp-persons-feature-title">关联网络</div>
                    <div className="lp-persons-feature-desc">
                      机构任职、双边互动、智库引用,一目了然
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </ScrollReveal>
        </div>
      </div>
    </section>
  );
}
