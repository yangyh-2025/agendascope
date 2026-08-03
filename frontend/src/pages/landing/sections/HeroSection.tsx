import { Suspense, lazy, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import ScrollReveal from "../components/ScrollReveal";

const Globe = lazy(() => import("../components/Globe"));

interface HeroSectionProps {
  isAuthenticated: boolean;
}

/** 检测 WebGL 是否可用;移动端小屏也走降级。 */
function useShouldRenderGlobe(): boolean {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const isMobile = window.matchMedia("(max-width: 768px)").matches;
    if (isMobile) return;
    try {
      const canvas = document.createElement("canvas");
      const gl =
        canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
      if (gl) setOk(true);
    } catch {
      /* 忽略,保持 false */
    }
  }, []);
  return ok;
}

export default function HeroSection({ isAuthenticated }: HeroSectionProps) {
  const showGlobe = useShouldRenderGlobe();
  return (
    <section className="lp-hero">
      <div className="lp-hero-bg" aria-hidden="true">
        <div className="lp-hero-grid" />
        <div className="lp-hero-glow lp-hero-glow-1" />
        <div className="lp-hero-glow lp-hero-glow-2" />
      </div>

      <header className="lp-nav">
        <div className="lp-nav-brand">
          <img src="/logo.png" alt="观澜" className="lp-nav-logo" />
          <span className="lp-nav-name">观澜 · AgendaScope</span>
        </div>
        <nav className="lp-nav-links">
          <a href="#capabilities">能力</a>
          <a href="#propagation">溯源</a>
          <a href="#architecture">架构</a>
          <a href="#reliability">可靠性</a>
          <Link to={isAuthenticated ? "/dashboard" : "/login"} className="lp-nav-cta">
            {isAuthenticated ? "回到看板" : "登录"}
          </Link>
        </nav>
      </header>

      <div className="lp-hero-content">
        <div className="lp-hero-text">
          <ScrollReveal>
            <div className="lp-hero-eyebrow">
              <span className="lp-hero-eyebrow-dot" />
              全球主流媒体舆情实时监控
            </div>
          </ScrollReveal>
          <ScrollReveal delay={120}>
            <h1 className="lp-hero-title">
              看见 <em>议程</em> 如何
              <br />
              在全球 <em>108 国</em> 流动
            </h1>
          </ScrollReveal>
          <ScrollReveal delay={240}>
            <p className="lp-hero-subtitle">
              面向国家安全与国际关系研究机构的议程设置识别系统。
              <br />
              从首发源判定到跨国传播链路,让每一次舆论涌动都有据可查。
            </p>
          </ScrollReveal>
          <ScrollReveal delay={360}>
            <div className="lp-hero-actions">
              <Link
                to={isAuthenticated ? "/dashboard" : "/login"}
                className="lp-btn lp-btn-primary"
              >
                {isAuthenticated ? "回到看板" : "进入系统"}
                <span aria-hidden="true">→</span>
              </Link>
              <a href="#capabilities" className="lp-btn lp-btn-ghost">
                探索能力
              </a>
            </div>
          </ScrollReveal>
          <ScrollReveal delay={480}>
            <div className="lp-hero-meta">
              <span>Docker Compose 私有化交付</span>
              <span aria-hidden="true">·</span>
              <span>2C2G 低配可跑</span>
              <span aria-hidden="true">·</span>
              <span>LLM 云端合规通道</span>
            </div>
          </ScrollReveal>
        </div>

        <div className="lp-hero-globe">
          {showGlobe ? (
            <Suspense fallback={<div className="lp-globe-fallback" />}>
              <Globe />
            </Suspense>
          ) : (
            <div className="lp-globe-fallback" aria-hidden="true" />
          )}
        </div>
      </div>

      <div className="lp-hero-scroll" aria-hidden="true">
        <span>SCROLL</span>
        <div className="lp-hero-scroll-line" />
      </div>
    </section>
  );
}
