import { useEffect } from "react";
import { useAuthStore } from "../../stores/auth";
import HeroSection from "./sections/HeroSection";
import StatsSection from "./sections/StatsSection";
import CollectionSection from "./sections/CollectionSection";
import HotTopicsSection from "./sections/HotTopicsSection";
import PersonsSection from "./sections/PersonsSection";
import AlertsSection from "./sections/AlertsSection";
import PropagationSection from "./sections/PropagationSection";
import ArchitectureSection from "./sections/ArchitectureSection";
import CtaSection from "./sections/CtaSection";
import "./LandingPage.css";

export default function LandingPage() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  useEffect(() => {
    document.title = "观澜 · AgendaScope — 全球议程设置监控平台";
    document.documentElement.classList.add("lp-root");
    return () => {
      document.documentElement.classList.remove("lp-root");
    };
  }, []);

  return (
    <div className="lp">
      <HeroSection isAuthenticated={isAuthenticated} />
      <StatsSection />
      <PropagationSection />
      <PersonsSection />
      <CollectionSection />
      <HotTopicsSection />
      <AlertsSection />
      <ArchitectureSection />
      <CtaSection isAuthenticated={isAuthenticated} />
    </div>
  );
}
