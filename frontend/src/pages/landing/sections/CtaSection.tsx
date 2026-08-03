import { Link } from "react-router-dom";
import { useState } from "react";
import ScrollReveal from "../components/ScrollReveal";
import ContactModal from "../components/ContactModal";

interface CtaSectionProps {
  isAuthenticated: boolean;
}

const CONTACT_EMAIL = "yangyuhang2667@163.com";

export default function CtaSection({ isAuthenticated }: CtaSectionProps) {
  const [modalOpen, setModalOpen] = useState(false);

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

        <ScrollReveal delay={300}>
          <div className="lp-cta-contact">
            <a
              href="https://github.com/yangyh-2025/agendascope"
              target="_blank"
              rel="noopener noreferrer"
              className="lp-cta-contact-item"
            >
              <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/>
              </svg>
              GitHub
            </a>
            <button
              type="button"
              onClick={() => setModalOpen(true)}
              className="lp-cta-contact-item lp-cta-contact-btn"
            >
              <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
                <path d="M1.5 3h13a.5.5 0 0 1 .5.5v9a.5.5 0 0 1-.5.5h-13a.5.5 0 0 1-.5-.5v-9a.5.5 0 0 1 .5-.5Zm.7 1.24 5.8 4.6 5.8-4.6V4H2.2v.24ZM14 5.05l-5.62 4.46a.75.75 0 0 1-.76 0L2 5.05V12h12V5.05Z"/>
              </svg>
              联系我们
            </button>
          </div>
        </ScrollReveal>

        <footer className="lp-footer">
          <div className="lp-footer-brand">
            <img src="/logo.svg" alt="观澜" className="lp-footer-logo" />
            <div className="lp-footer-brand-text">
              <span className="lp-footer-brand-name">观澜 · AgendaScope</span>
              <span className="lp-footer-brand-org">
                <a
                  href="https://www.uir.cn/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="lp-footer-org-link"
                >
                  国际关系学院
                </a>
                {" · "}
                国家安全计算模拟实验室
              </span>
            </div>
          </div>
          <div className="lp-footer-meta">
            <span>© 2026 AgendaScope</span>
            <span aria-hidden="true">·</span>
            <a
              href="https://github.com/yangyh-2025/agendascope"
              target="_blank"
              rel="noopener noreferrer"
            >
              GitHub
            </a>
            <span aria-hidden="true">·</span>
            <button
              type="button"
              onClick={() => setModalOpen(true)}
              className="lp-footer-link-btn"
            >
              联系我们
            </button>
          </div>
        </footer>
      </div>

      <ContactModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        email={CONTACT_EMAIL}
      />
    </section>
  );
}
