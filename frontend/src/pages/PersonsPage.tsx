/** 监控对象（关键实体社交网络）：50 精品实体 + LLM 关系图谱。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { ApiError } from "../api/client";
import {
  fetchRelationEvidences,
  fetchWatchlistGraph,
  type RelationDetail,
  type WatchlistGraph,
  type WatchlistLink,
} from "../api/watchlist";
import "./PersonsPage.css";

const CATEGORY_COLORS: Record<string, string> = {
  "美国白宫": "#c8102e",
  "美国外交": "#1a4fa0",
  "美国经济": "#16a34a",
  "美国国防": "#7c3aed",
  "美国国安": "#9333ea",
  "美国情报": "#dc2626",
  "美国智库": "#0891b2",
  "欧盟决策": "#2563eb",
  "欧盟外交": "#3b82f6",
  "欧盟经济": "#60a5fa",
  "北约": "#1e40af",
  "俄罗斯外交": "#ea580c",
  "俄罗斯国安": "#c2410c",
  "俄罗斯情报": "#9a3412",
  "俄罗斯经济": "#f59e0b",
  "中东决策": "#d4a017",
  "中东外交": "#ca8a04",
  "中东国安": "#a16207",
  "中东情报": "#854d0e",
  "印太外交": "#059669",
  "印太国防": "#047857",
  "印太国安": "#065f46",
  "联合国": "#6366f1",
  "多边经济": "#8b5cf6",
  "全球南方": "#ec4899",
  "外围": "#9ca3af",
  "其他": "#6b7280",
};

const RELATION_LABEL: Record<string, string> = {
  meets: "会面",
  sanctions: "制裁",
  appoints: "任命",
  criticizes: "批评",
  supports: "支持",
  opposes: "反对",
  allies_with: "结盟",
  member_of: "任职",
  advises: "顾问",
  funds: "资助",
  invests_in: "投资",
  signals_support: "释放支持信号",
  travelled_to: "访问",
  statement_about: "声明谈及",
  family_of: "亲属",
  other: "其他",
};

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function PersonsPage() {
  const [graph, setGraph] = useState<WatchlistGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [includePeripheral, setIncludePeripheral] = useState(false);
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [selectedRelation, setSelectedRelation] = useState<WatchlistLink | null>(null);
  const [evidences, setEvidences] = useState<RelationDetail | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [search, setSearch] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchWatchlistGraph({ include_peripheral: includePeripheral })
      .then(setGraph)
      .catch((e) => setError(errMsg(e, "监控对象图谱加载失败")))
      .finally(() => setLoading(false));
  }, [includePeripheral]);

  useEffect(() => {
    load();
  }, [load]);

  // 点边 → 拉证据
  useEffect(() => {
    if (!selectedRelation) {
      setEvidences(null);
      return;
    }
    setEvidenceLoading(true);
    fetchRelationEvidences(selectedRelation.id, { page_size: 50 })
      .then(setEvidences)
      .catch((e) => setError(errMsg(e, "证据加载失败")))
      .finally(() => setEvidenceLoading(false));
  }, [selectedRelation]);

  const categories = useMemo(() => {
    if (!graph) return [];
    return Array.from(new Set(graph.nodes.map((n) => n.category))).sort();
  }, [graph]);

  const chartOption = useMemo<EChartsOption | null>(() => {
    if (!graph) return null;
    let nodes = graph.nodes;
    let links = graph.links;
    if (selectedCategory) {
      const keep = new Set(
        nodes.filter((n) => n.category === selectedCategory).map((n) => n.id),
      );
      // 保留两类节点：选中类别的节点 + 与它们相连的种子节点
      links.forEach((l) => {
        if (keep.has(l.source)) keep.add(l.target);
        if (keep.has(l.target)) keep.add(l.source);
      });
      nodes = nodes.filter((n) => keep.has(n.id));
      links = links.filter((l) => keep.has(l.source) && keep.has(l.target));
    }
    if (search.trim()) {
      // 搜索命中节点高亮，其他不删
      // 搜索逻辑由 ECharts emphasis 处理
    }
    return {
      backgroundColor: "transparent",
      tooltip: {
        formatter: (params: { dataType: string; data: { name?: string; relation_type?: string; evidence_count?: number; confidence?: number } }) => {
          if (params.dataType === "edge") {
            const d = params.data;
            return `${RELATION_LABEL[d.relation_type ?? "other"] ?? d.relation_type}<br/>证据 ${d.evidence_count} 条 · 置信度 ${(Number(d.confidence ?? 0) * 100).toFixed(0)}%`;
          }
          return params.data.name ?? "";
        },
      },
      series: [
        {
          type: "graph",
          layout: "force",
          roam: true,
          scaleLimit: { min: 0.4, max: 3 },
          data: nodes.map((n) => ({
            id: n.id,
            name: n.name,
            value: n.priority,
            symbolSize: n.is_seed
              ? Math.max(18, 14 + n.priority / 6)
              : 10,
            itemStyle: {
              color: CATEGORY_COLORS[n.category] ?? CATEGORY_COLORS["其他"],
              opacity: n.is_seed ? 1 : 0.5,
              borderColor: "rgba(255,255,255,0.4)",
              borderWidth: n.is_seed ? 1.5 : 0.5,
            },
            label: {
              show: n.is_seed,
              color: "#1f2d3d",
              fontSize: n.priority >= 90 ? 12 : 10,
              fontWeight: n.priority >= 90 ? 700 : 500,
            },
            category: n.category,
          })),
          links: links.map((l) => ({
            id: l.id,
            source: l.source,
            target: l.target,
            relation_type: l.relation_type,
            confidence: l.confidence,
            evidence_count: l.evidence_count,
            lineStyle: {
              width: Math.min(4, 1 + l.evidence_count / 3),
              opacity: Math.max(0.3, l.confidence),
              color: "#1a4fa0",
              curveness: 0.18,
            },
          })),
          force: {
            repulsion: 380,
            edgeLength: [60, 140],
            gravity: 0.15,
            friction: 0.3,
            layoutAnimation: true,
          },
          label: { position: "bottom", distance: 4 },
          emphasis: {
            focus: "adjacency",
            lineStyle: { width: 4 },
          },
        },
      ],
    };
  }, [graph, selectedCategory, search]);

  if (error) {
    return (
      <div className="persons-page">
        <header className="page-header">
          <h1 className="page-title">监控对象</h1>
        </header>
        <p className="page-error" role="alert">{error}</p>
      </div>
    );
  }

  return (
    <div className="persons-page watchlist-page">
      <header className="page-header">
        <div>
          <h1 className="page-title">监控对象 · 关键实体社交网络</h1>
          <p className="page-desc">
            50 个精品关键人物与机构。每条关系边都有新闻证据支撑，点击边查看原始报道。
          </p>
        </div>
        <div className="watchlist-header-actions">
          <label className="watchlist-toggle">
            <input
              type="checkbox"
              checked={includePeripheral}
              onChange={(e) => setIncludePeripheral(e.target.checked)}
            />
            展开外围实体
          </label>
        </div>
      </header>

      <div className="watchlist-body">
        <div className="watchlist-graph-wrap">
          <div className="watchlist-toolbar">
            <select
              value={selectedCategory}
              onChange={(e) => setSelectedCategory(e.target.value)}
            >
              <option value="">全部分类</option>
              {categories.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <input
              type="search"
              placeholder="搜索实体名..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {graph && (
              <span className="watchlist-stats">
                {graph.total_nodes} 节点 · {graph.total_links} 边
              </span>
            )}
          </div>
          {loading && <p className="page-loading">加载中…</p>}
          {!loading && chartOption && (
            <ReactECharts
              option={chartOption}
              style={{ width: "100%", height: "640px" }}
              notMerge
              lazyUpdate
              onEvents={{
                click: (params: { dataType?: string; data?: { id?: string } }) => {
                  if (params.dataType === "edge" && params.data?.id) {
                    const link = graph?.links.find((l) => l.id === params.data?.id);
                    if (link) setSelectedRelation(link);
                  }
                },
              }}
            />
          )}
          {!loading && graph && graph.total_links === 0 && (
            <p className="page-loading" style={{ padding: 40 }}>
              暂无关系统计。等待每日跑批从新闻中抽取实体关系。
            </p>
          )}
        </div>

        {/* 右侧证据抽屉 */}
        {selectedRelation && (
          <aside className="watchlist-evidence-drawer">
            <div className="watchlist-evidence-header">
              <div>
                <div className="watchlist-evidence-title">
                  {RELATION_LABEL[selectedRelation.relation_type] ?? selectedRelation.relation_type}
                </div>
                <div className="watchlist-evidence-meta">
                  证据 {selectedRelation.evidence_count} 条 ·
                  置信度 {(selectedRelation.confidence * 100).toFixed(0)}%
                </div>
              </div>
              <button
                type="button"
                className="watchlist-evidence-close"
                onClick={() => setSelectedRelation(null)}
                aria-label="关闭"
              >
                ✕
              </button>
            </div>
            <div className="watchlist-evidence-body">
              {evidenceLoading && <p>证据加载中…</p>}
              {!evidenceLoading && evidences && evidences.items.length === 0 && (
                <p className="watchlist-evidence-empty">暂无证据</p>
              )}
              {!evidenceLoading && evidences && evidences.items.map((ev) => (
                <article key={ev.evidence_id} className="watchlist-evidence-item">
                  <blockquote className="watchlist-evidence-quote">
                    “{ev.evidence_quote}”
                  </blockquote>
                  <div className="watchlist-evidence-article">
                    <a
                      href={ev.article_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="watchlist-evidence-link"
                    >
                      {ev.article_title_translated || ev.article_title}
                    </a>
                    <div className="watchlist-evidence-src">
                          {ev.source_name} · {ev.source_country_code} ·
                          {" "}{ev.published_at?.slice(0, 16).replace("T", " ") ?? "—"}
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
