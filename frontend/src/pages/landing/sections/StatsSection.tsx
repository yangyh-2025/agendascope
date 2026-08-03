import CountUp from "../components/CountUp";
import ScrollReveal from "../components/ScrollReveal";
import { HERO_STATS } from "../mock/stats";

export default function StatsSection() {
  return (
    <section className="lp-stats">
      <div className="lp-container">
        <div className="lp-stats-grid">
          {HERO_STATS.map((s, i) => (
            <ScrollReveal key={s.label} delay={i * 100}>
              <div className="lp-stat-card">
                <div className="lp-stat-value">
                  <CountUp end={s.value} suffix={s.suffix} />
                </div>
                <div className="lp-stat-label">{s.label}</div>
                <div className="lp-stat-hint">{s.hint}</div>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </div>
    </section>
  );
}
