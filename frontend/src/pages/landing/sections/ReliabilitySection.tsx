import ScrollReveal from "../components/ScrollReveal";
import { RELIABILITY_METRICS } from "../mock/stats";

export default function ReliabilitySection() {
  return (
    <section className="lp-section lp-section-alt" id="reliability">
      <div className="lp-container">
        <ScrollReveal>
          <div className="lp-section-eyebrow">可靠性验证</div>
          <h2 className="lp-section-title">
            不是 PPT 上的数字,
            <br />
            是生产回放跑出来的结果
          </h2>
          <p className="lp-section-lede">
            每一项指标都来自真实生产环境的回放测试与运行日志。
          </p>
        </ScrollReveal>

        <div className="lp-rel-grid">
          {RELIABILITY_METRICS.map((m, i) => (
            <ScrollReveal key={m.label} delay={(i % 3) * 100}>
              <div className="lp-rel-card">
                <div className="lp-rel-value">{m.value}</div>
                <div className="lp-rel-label">{m.label}</div>
                <p className="lp-rel-desc">{m.description}</p>
              </div>
            </ScrollReveal>
          ))}
        </div>

        <ScrollReveal delay={200}>
          <div className="lp-rel-quote">
            <div className="lp-rel-quote-mark" aria-hidden="true">"</div>
            <p>
              议题归并的误拆率、误并率、归并率,生产默认纯向量策略在 24 个真实案例回放中
              全部通过阈值——LLM 二次确认保留为 opt-in 能力,不作为默认。
            </p>
            <footer>— 生产回放报告,v1.3.x</footer>
          </div>
        </ScrollReveal>
      </div>
    </section>
  );
}
