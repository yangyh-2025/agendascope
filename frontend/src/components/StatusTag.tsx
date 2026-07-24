import type { SourceStatus } from "../api/sources";
import "./StatusTag.css";

const STATUS_META: Record<SourceStatus, { label: string; className: string }> = {
  active: { label: "正常", className: "status-tag status-active" },
  degraded: { label: "降级", className: "status-tag status-degraded" },
  failed: { label: "失败", className: "status-tag status-failed" },
};

export default function StatusTag({ status }: { status: SourceStatus }) {
  const meta = STATUS_META[status] ?? { label: status, className: "status-tag" };
  return <span className={meta.className}>{meta.label}</span>;
}
