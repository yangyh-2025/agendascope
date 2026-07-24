import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { listSources, type SourceListItem } from "../api/sources";
import "./DashboardPage.css";

interface CountryStat {
  countryCode: string;
  total: number;
  active: number;
  degraded: number;
  failed: number;
}

const PAGE_SIZE = 100;

/** 拉取全部媒体源（分页循环），按国家聚合为覆盖总览统计。 */
async function fetchAllSources(): Promise<SourceListItem[]> {
  const first = await listSources({ page: 1, page_size: PAGE_SIZE });
  const items = [...first.items];
  const pages = Math.ceil(first.total / PAGE_SIZE);
  for (let page = 2; page <= pages; page += 1) {
    const res = await listSources({ page, page_size: PAGE_SIZE });
    items.push(...res.items);
  }
  return items;
}

export default function DashboardPage() {
  const [sources, setSources] = useState<SourceListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAllSources()
      .then((items) => {
        if (!cancelled) setSources(items);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "加载媒体源数据失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const stats = useMemo<CountryStat[]>(() => {
    if (!sources) return [];
    const map = new Map<string, CountryStat>();
    for (const s of sources) {
      const stat =
        map.get(s.country_code) ??
        { countryCode: s.country_code, total: 0, active: 0, degraded: 0, failed: 0 };
      stat.total += 1;
      stat[s.status] += 1;
      map.set(s.country_code, stat);
    }
    return [...map.values()].sort((a, b) => b.total - a.total);
  }, [sources]);

  return (
    <section>
      <h2 className="page-title">媒体源覆盖总览</h2>
      <p className="page-desc">按国家/地区统计已接入媒体源数量与健康分布（数据来自媒体源库实时查询）。</p>

      {error && <p className="page-error" role="alert">{error}</p>}
      {!error && !sources && <p className="page-loading">加载中…</p>}

      {sources && stats.length === 0 && (
        <p className="page-loading">媒体源库为空，请先在“媒体源”页接入媒体源。</p>
      )}

      <div className="stat-grid">
        {stats.map((stat) => (
          <div key={stat.countryCode} className="stat-card">
            <div className="stat-card-head">
              <span className="stat-country">{stat.countryCode}</span>
              <span className="stat-total">{stat.total} 个源</span>
            </div>
            <div className="stat-status-row">
              <span className="stat-pill ok">正常 {stat.active}</span>
              <span className="stat-pill warn">降级 {stat.degraded}</span>
              <span className="stat-pill bad">失败 {stat.failed}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
