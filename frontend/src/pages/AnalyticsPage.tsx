/** 分析看板页(T4.10):跨国对比视图 + "统计关联≠因果"提示。 */
import { useState } from "react";
import { ApiError } from "../api/client";
import { fetchTopicCompare, type TopicCompareResult } from "../api/snapshots";
import { COUNTRIES, countryLabel } from "../api/meta";
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
      .catch((err) => setError(err instanceof ApiError ? err.message : "对比数据加载失败,请稍后重试"));
  };

  const toggleCountry = (code: string) => {
    setCountries((prev) => prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code].slice(0, 4));
  };

  return (
    <div className="analytics-page">
      <header className="page-header">
        <div>
          <h1 className="page-title">跨国对比分析</h1>
          <p className="page-desc">选择 2–4 个国家,对比近 7 天的报道量与首位议题差异。</p>
        </div>
      </header>

      <div className="selector-panel">
        <div className="selector-row">
          <span className="selector-label">选择国家</span>
          <div className="country-selector">
            {COUNTRIES.slice(0, 20).map((c) => (
              <button
                key={c.code}
                className={`country-chip ${countries.includes(c.code) ? "active" : ""}`}
                onClick={() => toggleCountry(c.code)}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>
        <div className="selector-actions">
          <span className="selector-hint">已选 {countries.length} 个,可选 2–4 个</span>
          <button className="compare-btn" onClick={fetch}>开始对比</button>
        </div>
      </div>

      {error && <p className="page-error" role="alert">{error}</p>}

      {result && (
        <div className="compare-results">
          <p className="disclaimer">{result.disclaimer}</p>
          <div className="compare-grid">
            {result.per_country?.map((pc) => (
              <div key={pc.country_code} className="country-panel">
                <h3>{countryLabel(pc.country_code)}</h3>
                <div className="stat-row">
                  <span>总报道</span>
                  <b>{pc.total_articles}</b>
                </div>
                <div className="stat-row">
                  <span>首位议题</span>
                  <b className="stat-topic">{pc.top_topic_name || "—"}</b>
                </div>
                <div className="stat-row">
                  <span>覆盖</span>
                  <b className={pc.coverage === "low" ? "low" : "ok"}>{pc.coverage}</b>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
