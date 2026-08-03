import { Link } from "react-router-dom";
import ScrollReveal from "../components/ScrollReveal";

interface CtaSectionProps {
  isAuthenticated: boolean;
}

export default function CtaSection({ isAuthenticated }: CtaSectionProps) {
  return (
    <section className="lp-cta">
      <div className="lp-container">
        <ScrollReveal>
          <h2 className="lp-cta-title">
            每一次全球舆论涌动,
            <br />
            都值得被看清
          </h2>
        </ScrollReveal>
        <ScrollReveal delay={120}>
          <p className="lp-cta-lede">
            进入系统,看看今天 108 国的媒体都在说什么。
          </p>
        </ScrollReveal>
        <ScrollReveal delay={240}>
          <div className="lp-cta-actions">
            <Link
              to={isAuthenticated ? "/dashboard" : "/login"}
              className="lp-btn lp-btn-primary lp-btn-lg"
            >
              {isAuthenticated ? "回到看板" : "进入系统"}
              <span aria-hidden="true">→</span>
            </Link>
          </div>
        </ScrollReveal>

        <footer className="lp-footer">
          <div className="lp-footer-brand">
            <img src="/logo.png" alt="观澜" className="lp-footer-logo" />
            <span>观澜 · AgendaScope</span>
          </div>
          <div className="lp-footer-meta">
            <span>© 2026 AgendaScope</span>
            <span aria-hidden="true">·</span>
            <span>面向国家安全与国际关系研究</span>
          </div>
        </footer>
      </div>
    </section>
  );
}
