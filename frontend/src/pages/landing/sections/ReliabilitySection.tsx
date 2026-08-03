import ScrollReveal from "../components/ScrollReveal";
import { RELIABILITY_METRICS } from "../mock/stats";

/** 把指标 value 字符串转成 0-100 进度(仅用于环形显示,非百分比用 -1 表示隐藏)。 */
function toProgress(value: string): number {
  if (value.endsWith("%")) {
    const n = parseFloat(value.replace("%", ""));
    return Number.isFinite(n) ? n : -1;
  }
  // 8x / 0.5s / 0 等非百分比,用 -1 表示不显示环
  return -1;
}

function RadialRing({ progress }: { progress: number }) {
  // progress 0-100
  const clamped = Math.max(0, Math.min(100, progress));
  const angle = (clamped / 100) * 360;
  return (
    <div
      className="lp-rel-ring"
      style={{
        background: `conic-gradient(#4f7fff 0deg, #8b5cf6 ${angle}deg, rgba(120,160,255,0.12) ${angle}deg, rgba(120,160,255,0.12) 360deg)`,
      }}
      aria-hidden="true"
    >
      <div className="lp-rel-ring-inner" />
    </div>
  );
}

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
          {RELIABILITY_METRICS.map((m, i) => {
            const progress = toProgress(m.value);
            return (
              <ScrollReveal key={m.label} delay={(i % 3) * 100}>
                <div className="lp-rel-card">
                  {progress >= 0 && (
                    <div className="lp-rel-ring-wrap">
                      <RadialRing progress={progress} />
                      <div className="lp-rel-value lp-rel-value-overlay">{m.value}</div>
                    </div>
                  )}
                  {progress < 0 && <div className="lp-rel-value">{m.value}</div>}
                  <div className="lp-rel-label">{m.label}</div>
                  <p className="lp-rel-desc">{m.description}</p>
                </div>
              </ScrollReveal>
            );
          })}
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
