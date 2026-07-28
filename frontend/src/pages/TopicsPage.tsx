/** 议题列表页（T4.7）：筛选 / 排序 / 分页 / 议题卡网格。 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { listTopics, LifecycleState, TopicListItem, TopicSort } from "../api/topics";
import { COUNTRIES, TOPIC_CATEGORIES, LIFECYCLE_LABEL } from "../api/meta";
import DegradedBadge from "../components/DegradedBadge";
import { degradedKindsOf } from "../components/degraded";
import "./TopicsPage.css";

const SORT_OPTIONS: { value: TopicSort; label: string }[] = [
  { value: "salience", label: "显著性" },
  { value: "article_count", label: "报道量" },
  { value: "last_seen_at", label: "最近活跃" },
];

export default function TopicsPage() {
  const nav = useNavigate();
  const [items, setItems] = useState<TopicListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [country, setCountry] = useState("");
  const [lifecycle, setLifecycle] = useState<string>("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState<TopicSort>("salience");
  const [error, setError] = useState<string | null>(null);
  const [listDegraded, setListDegraded] = useState(false);

  const fetch = useCallback(() => {
    setError(null);
    listTopics({ country_code: country || undefined, lifecycle_state: (lifecycle || undefined) as LifecycleState | undefined, topic_category: category || undefined, sort, page, page_size: 20 }).then((r) => {
      setItems(r.items); setTotal(r.total); setListDegraded(Boolean(r.degraded));
    }).catch((err) => {
      setError(err instanceof ApiError ? err.message : "议题列表加载失败，请稍后重试");
    });
  }, [country, lifecycle, category, sort, page]);

  useEffect(() => { fetch(); }, [fetch]);

  return (
    <div className="topics-page">
      <h1>议题</h1>
      <div className="filters">
        <select value={country} onChange={(e) => { setCountry(e.target.value); setPage(1); }}>
          <option value="">全部国家</option>
          {COUNTRIES.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
        </select>
        <select value={lifecycle} onChange={(e) => { setLifecycle(e.target.value); setPage(1); }}>
          <option value="">全部状态</option>
          {Object.entries(LIFECYCLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }}>
          <option value="">全部分类</option>
          {TOPIC_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={sort} onChange={(e) => { setSort(e.target.value as TopicSort); setPage(1); }}>
          {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>
      {error && <p className="page-error" role="alert">{error}</p>}
      {listDegraded && (
        <p className="list-degraded-row">
          <DegradedBadge kind="snapshot_outdated" reason="议题列表处于降级口径，显著性排序可能滞后" />
        </p>
      )}
      <div className="topic-grid">
        {items.map((t) => (
          <div key={t.id} className="topic-card" onClick={() => nav(`/topics/${t.id}`)}>
            <div className="topic-card-header">
              <h3>{t.name}</h3>
              <span className={`category-tag cat-${t.topic_category}`}>{t.topic_category}</span>
            </div>
            <p className="topic-summary">{t.salience_score.toFixed(2)} 显著性 · {t.article_count_24h ?? t.article_count ?? 0} 篇{t.media_count != null ? ` · ${t.media_count} 源` : ""}</p>
            <div className="topic-card-footer">
              <span className={`lifecycle-tag ${t.lifecycle_state}`}>{LIFECYCLE_LABEL[t.lifecycle_state]}</span>
              <span className="country-scope">{t.country_scope?.join(", ")}</span>
              {t.has_agenda_event && <span className="agenda-event-tag">议程事件</span>}
              {degradedKindsOf(t).map((k) => (
                <DegradedBadge key={k} kind={k} />
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="pagination">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
        <span>{page} / {Math.ceil(total / 20)}（共 {total} 条）</span>
        <button disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button>
      </div>
    </div>
  );
}
