/** 分析看板页（T4.10/T4.11）：跨国对比 + 人物监测。 */
import { useState } from "react";
import { request } from "../api/client";
import { COUNTRIES } from "../api/meta";
import "./AnalyticsPage.css";

export default function AnalyticsPage() {
  const [countries, setCountries] = useState(["CN", "US"]);
  const [result, setResult] = useState<any>(null);

  const fetch = () => {
    const cc = countries.filter(Boolean).slice(0, 4).join(",");
    request(`/api/v1/snapshots/compare?countries=${cc}&days=7`).then(setResult).catch(console.error);
  };

  const toggleCountry = (code: string) => {
    setCountries((prev) => prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code].slice(0, 4));
  };

  return (
    <div className="analytics-page">
      <h1>跨国对比分析</h1>
      <div className="country-selector">
        {COUNTRIES.slice(0, 20).map((c) => (
          <button key={c.code} className={`country-chip ${countries.includes(c.code) ? "active" : ""}`} onClick={() => toggleCountry(c.code)}>{c.label}</button>
        ))}
        <button className="compare-btn" onClick={fetch}>对比</button>
      </div>
      {result && (
        <div className="compare-results">
          <p className="disclaimer">{result.disclaimer}</p>
          <div className="compare-grid">
            {result.per_country?.map((pc: any) => (
              <div key={pc.country_code} className="country-panel">
                <h3>{pc.country_code}</h3>
                <div className="stat-row"><span>总报道</span><b>{pc.total_articles}</b></div>
                <div className="stat-row"><span>首位议题</span><b>{pc.top_topic_name || "—"}</b></div>
                <div className="stat-row"><span>覆盖</span><b className={pc.coverage === "low" ? "low" : ""}>{pc.coverage}</b></div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
