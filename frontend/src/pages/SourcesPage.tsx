import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";
import {
  listSources,
  type SourceListItem,
  type SourceListPage,
} from "../api/sources";
import { countryLabel } from "../api/meta";
import StatusTag from "../components/StatusTag";
import SourceCreatePanel from "../components/SourceCreatePanel";
import "./SourcesPage.css";

const PAGE_SIZE = 20;

const MEDIA_TYPE_LABEL: Record<string, string> = {
  newspaper: "报纸",
  agency: "通讯社",
  broadcast: "广电",
  online: "网络媒体",
};

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function SourceTable({ items }: { items: SourceListItem[] }) {
  return (
    <table className="sources-table">
      <thead>
        <tr>
          <th>名称</th>
          <th>国家</th>
          <th>类型</th>
          <th>健康状态</th>
          <th>最近采集时间</th>
        </tr>
      </thead>
      <tbody>
        {items.map((s) => (
          <tr key={s.id}>
            <td>
              <div className="source-name">{s.name}</div>
              {s.name_zh && <div className="source-name-zh">{s.name_zh}</div>}
            </td>
            <td>{countryLabel(s.country_code)}</td>
            <td>{MEDIA_TYPE_LABEL[s.media_type] ?? s.media_type}</td>
            <td>
              <StatusTag status={s.status} />
              {s.health_24h?.success_rate != null && (
                <span className="source-health">
                  24h 成功率 {(s.health_24h.success_rate * 100).toFixed(0)}%
                </span>
              )}
            </td>
            <td>{formatTime(s.last_success_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function SourcesPage() {
  const [data, setData] = useState<SourceListPage | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback((targetPage: number) => {
    setLoading(true);
    setError(null);
    listSources({ page: targetPage, page_size: PAGE_SIZE })
      .then((res) => {
        setData(res);
        setPage(targetPage);
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "加载媒体源列表失败");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load(1);
  }, [load]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <section>
      <div className="sources-header">
        <div>
          <h2 className="page-title">媒体源管理</h2>
          <p className="page-desc">
            共 {data?.total ?? "…"} 个媒体源。新增源支持粘贴 URL 试运行后再入库（自助配源）。
          </p>
        </div>
        <button onClick={() => setShowCreate((v) => !v)}>
          {showCreate ? "收起新增面板" : "新增媒体源"}
        </button>
      </div>

      {showCreate && (
        <SourceCreatePanel
          onCreated={() => {
            setShowCreate(false);
            load(1);
          }}
        />
      )}

      {error && <p className="page-error" role="alert">{error}</p>}
      {loading && <p className="page-loading">加载中…</p>}

      {!loading && !error && data && data.items.length === 0 && (
        <p className="page-loading">暂无媒体源。</p>
      )}

      {!loading && !error && data && data.items.length > 0 && (
        <>
          <SourceTable items={data.items} />
          <div className="sources-pager">
            <button
              className="as-btn-ghost"
              disabled={page <= 1}
              onClick={() => load(page - 1)}
            >
              上一页
            </button>
            <span className="sources-pager-info">
              第 {page} / {totalPages} 页
            </span>
            <button
              className="as-btn-ghost"
              disabled={page >= totalPages}
              onClick={() => load(page + 1)}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
