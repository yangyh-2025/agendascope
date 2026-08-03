import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import ScrollReveal from "../components/ScrollReveal";
import { PERSON_DEMO } from "../mock/demos";

const TYPE_COLOR: Record<string, string> = {
  person: "#ff3b5c",
  org: "#4f7fff",
  country: "#8b5cf6",
  thinktank: "#2fa96b",
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
      symbolSize: n.id === "center" ? 60 : Math.max(20, Math.sqrt(n.mentions) * 3.5),
      itemStyle: {
        color: TYPE_COLOR[n.type],
        shadowBlur: n.id === "center" ? 24 : 12,
        shadowColor: TYPE_COLOR[n.type],
        borderColor: "rgba(255,255,255,0.2)",
        borderWidth: n.id === "center" ? 2 : 1,
      },
      label: {
        show: true,
        color: "#e8eef7",
        fontSize: n.id === "center" ? 14 : 11,
        fontWeight: n.id === "center" ? 700 : 500,
      },
      category: n.type,
    }));

    const links = PERSON_DEMO.links.map((l) => ({
      source: l.source,
      target: l.target,
      label: {
        show: true,
        formatter: l.label,
        color: "#9aa8c5",
        fontSize: 10,
      },
      lineStyle: {
        color: "rgba(120,160,255,0.35)",
        width: 1.2,
        curveness: 0.2,
      },
    }));

    return {
      backgroundColor: "transparent",
      series: [
        {
          type: "graph",
          layout: "force",
          roam: false,
          data: nodes,
          links: links,
          force: {
            repulsion: 400,
            edgeLength: [80, 140],
            gravity: 0.1,
            friction: 0.3,
          },
          label: {
            position: "bottom",
            distance: 6,
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
                style={{ width: "100%", height: "440px" }}
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
