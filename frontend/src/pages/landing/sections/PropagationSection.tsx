import ScrollReveal from "../components/ScrollReveal";
import { PROPAGATION_DEMO } from "../mock/stats";

export default function PropagationSection() {
  const maxLag = Math.max(...PROPAGATION_DEMO.map((n) => n.lagHours));
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

        <div className="lp-prop-demo">
          <div className="lp-prop-header">
            <span className="lp-prop-eyebrow">演示案例</span>
            <span className="lp-prop-topic">某国提出新经济框架 → 6 国跟随报道</span>
          </div>
          <div className="lp-prop-track">
            {PROPAGATION_DEMO.map((node, i) => {
              const left = (node.lagHours / maxLag) * 100;
              return (
                <ScrollReveal key={node.countryCode} delay={i * 140}>
                  <div
                    className={`lp-prop-node ${node.role === "origin" ? "lp-prop-origin" : ""}`}
                    style={{ left: `${left}%` }}
                  >
                    <div className="lp-prop-card">
                      <div className="lp-prop-country">
                        <span className="lp-prop-flag">{node.countryCode}</span>
                        <span className="lp-prop-country-name">{node.countryName}</span>
                        {node.role === "origin" && (
                          <span className="lp-prop-badge">首发源</span>
                        )}
                      </div>
                      <div className="lp-prop-time">
                        {node.time}
                        {node.lagHours > 0 && (
                          <span className="lp-prop-lag">+{node.lagHours.toFixed(1)}h</span>
                        )}
                      </div>
                      <div className="lp-prop-outlet">{node.outlet}</div>
                      <div className="lp-prop-headline">{node.headline}</div>
                    </div>
                  </div>
                </ScrollReveal>
              );
            })}
            <div className="lp-prop-line" aria-hidden="true" />
          </div>
          <div className="lp-prop-axis">
            <span>0h</span>
            <span>+{maxLag.toFixed(0)}h</span>
          </div>
        </div>

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
