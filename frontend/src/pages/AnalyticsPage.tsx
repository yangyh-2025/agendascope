/** 分析看板页（T4.10）：跨国对比视图 + “统计关联≠因果”提示。 */
import { useState } from "react";
import { ApiError } from "../api/client";
import { fetchTopicCompare, type TopicCompareResult } from "../api/snapshots";
import { COUNTRIES } from "../api/meta";
import "./AnalyticsPage.css";

export default function AnalyticsPage() {
  const [countries, setCountries] = useState(["CN", "US"]);
  const [result, setResult] = useState<TopicCompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetch = () => {
    setError(null);
    const cc = countries.filter(Boolean).slice(0, 4);
    if (cc.length < 2) {
      setError("请选择 2–4 个国家进行对比");
      return;
    }
    fetchTopicCompare(cc, 7)
      .then(setResult)
      .catch((err) => setError(err instanceof ApiError ? err.message : "对比数据加载失败，请稍后重试"));
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
      {error && <p className="page-error" role="alert">{error}</p>}
      {result && (
        <div className="compare-results">
          <p className="disclaimer">{result.disclaimer}</p>
          <div className="compare-grid">
            {result.per_country?.map((pc) => (
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
