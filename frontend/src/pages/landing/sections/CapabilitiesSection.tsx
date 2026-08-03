import ScrollReveal from "../components/ScrollReveal";
import { CAPABILITIES } from "../mock/capabilities";

export default function CapabilitiesSection() {
  return (
    <section className="lp-section" id="capabilities">
      <div className="lp-container">
        <ScrollReveal>
          <div className="lp-section-eyebrow">核心能力</div>
          <h2 className="lp-section-title">
            九大模块,构成完整的
            <br />
            议程设置识别闭环
          </h2>
          <p className="lp-section-lede">
            从全球采集到自我纠错,每个环节都为"识别谁先说出这句话"而设计。
          </p>
        </ScrollReveal>

        <div className="lp-cap-grid">
          {CAPABILITIES.map((cap, i) => (
            <ScrollReveal key={cap.title} delay={(i % 3) * 100}>
              <article className="lp-cap-card">
                <div className="lp-cap-icon" aria-hidden="true">
                  {cap.icon}
                </div>
                <h3 className="lp-cap-title">{cap.title}</h3>
                <div className="lp-cap-tagline">{cap.tagline}</div>
                <p className="lp-cap-desc">{cap.description}</p>
                <div className="lp-cap-metric">
                  <span className="lp-cap-metric-value">{cap.metric}</span>
                  <span className="lp-cap-metric-label">{cap.metricLabel}</span>
                </div>
              </article>
            </ScrollReveal>
          ))}
        </div>
      </div>
    </section>
  );
}
