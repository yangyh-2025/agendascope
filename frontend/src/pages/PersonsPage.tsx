/** 人物/机构监测页（T4.11）：发起信号列表（新表述、首发时间、跟进媒体数）。 */
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { COUNTRIES, countryLabel } from "../api/meta";
import {
  listPersonsOrgs,
  type EntityType,
  type PersonOrgListItem,
} from "../api/persons";
import "./PersonsPage.css";

const ENTITY_TYPE_LABEL: Record<EntityType, string> = {
  person: "人物",
  thinktank: "智库",
  intl_org: "国际组织",
  gov_body: "政府机构",
};

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function PersonsPage() {
  const [items, setItems] = useState<PersonOrgListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [entityType, setEntityType] = useState<string>("");
  const [country, setCountry] = useState("");
  const [monitoredOnly, setMonitoredOnly] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    listPersonsOrgs({
      entity_type: (entityType || undefined) as EntityType | undefined,
      country_code: country || undefined,
      monitored: monitoredOnly ? true : undefined,
      page,
      page_size: 20,
    })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((err) => setError(errMsg(err, "人物/机构数据加载失败")));
  }, [entityType, country, monitoredOnly, page]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="persons-page">
      <h1>人物 / 机构监测</h1>
      <div className="filters">
        <select value={entityType} onChange={(e) => { setEntityType(e.target.value); setPage(1); }}>
          <option value="">全部类型</option>
          {(Object.entries(ENTITY_TYPE_LABEL) as [EntityType, string][]).map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <select value={country} onChange={(e) => { setCountry(e.target.value); setPage(1); }}>
          <option value="">全部国家</option>
          {COUNTRIES.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
        </select>
        <label className="monitored-toggle">
          <input
            type="checkbox"
            checked={monitoredOnly}
            onChange={(e) => { setMonitoredOnly(e.target.checked); setPage(1); }}
          />
          仅看重点监测
        </label>
      </div>

      {error && <p className="page-error" role="alert">{error}</p>}
      {!error && items.length === 0 && <p className="page-loading">暂无监测对象</p>}

      <div className="entity-list">
        {items.map((e) => {
          const signals = e.first_utterances ?? [];
          const expanded = expandedId === e.id;
          return (
            <div key={e.id} className="entity-card">
              <button className="entity-head" onClick={() => setExpandedId(expanded ? null : e.id)}>
                <span className={`entity-type-tag et-${e.entity_type}`}>{ENTITY_TYPE_LABEL[e.entity_type] ?? e.entity_type}</span>
                <span className="entity-name">{e.name_zh || e.name}</span>
                {e.role_title && <span className="entity-role">{e.role_title}</span>}
                <span className="entity-country">{countryLabel(e.country_code)}</span>
                {e.monitored && <span className="monitored-tag">重点监测</span>}
                <span className="entity-signal-count">发起信号 {signals.length} 条 {expanded ? "▲" : "▼"}</span>
              </button>
              {expanded && (
                <div className="signal-list">
                  {signals.length === 0 && <p className="drawer-empty">暂无发起信号</p>}
                  {signals.map((s, i) => (
                    <div key={i} className="signal-item">
                      <p className="signal-quote">“{s.quote_zh || s.quote}”</p>
                      <div className="signal-meta">
                        <span>首发 {s.first_seen_at?.slice(0, 16).replace("T", " ") ?? "—"}</span>
                        <span>跟进媒体 {s.media_follow_count} 家</span>
                        {s.topic_name && <span>关联议题：{s.topic_name}</span>}
                        <span>置信度 {s.confidence}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="pagination">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
        <span>{page} / {Math.max(Math.ceil(total / 20), 1)}（共 {total} 条）</span>
        <button disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button>
      </div>
    </div>
  );
}
