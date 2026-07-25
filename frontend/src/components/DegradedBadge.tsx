/**
 * 降级状态与数据可信度标注（详细设计 8.5 失败降级 + PRD 4.13）。
 * 红色（中国红 #C8102E）按视觉规范仅保留给议程设置事件与预警，本组件不使用红色。
 */
import "./DegradedBadge.css";

export type DegradedKind =
  | "llm_degraded"
  | "cluster_degraded"
  | "translation_degraded"
  | "origin_needs_review"
  | "snapshot_outdated"
  | "coverage_low";

interface KindMeta {
  label: string;
  className: string;
  defaultReason: string;
}

const KIND_META: Record<DegradedKind, KindMeta> = {
  llm_degraded: {
    label: "LLM 降级中",
    className: "degraded-badge degraded-badge-warn",
    defaultReason: "LLM 不可用，议题命名/分类已切换为关键词兜底，恢复后自动回填。",
  },
  cluster_degraded: {
    label: "聚类降级中",
    className: "degraded-badge degraded-badge-warn",
    defaultReason: "BERTopic 聚类不可用，已切换为关键词匹配粗聚类，恢复后重新聚类回填。",
  },
  translation_degraded: {
    label: "跨语言匹配暂不可用",
    className: "degraded-badge degraded-badge-mute",
    defaultReason: "向量模型不可用，跨国议题匹配已暂停，界面展示原文。",
  },
  origin_needs_review: {
    label: "首发源待核实",
    className: "degraded-badge degraded-badge-caution",
    defaultReason: "最早记录为转载或时间戳异常，已加入人工复核队列，不自动触发告警。",
  },
  snapshot_outdated: {
    label: "快照校正中",
    className: "degraded-badge degraded-badge-info",
    defaultReason: "重聚类校正进行中，当前展示上一版快照，校正完成后自动刷新。",
  },
  coverage_low: {
    label: "数据覆盖不足",
    className: "degraded-badge degraded-badge-mute",
    defaultReason: "该国覆盖率置信度低于 70%，不使用旧数据冒充，请谨慎解读。",
  },
};

export interface DegradedBadgeProps {
  kind: DegradedKind;
  /** 悬浮 tooltip 中展示的详细原因；缺省用类型默认说明。 */
  reason?: string;
}

export default function DegradedBadge({ kind, reason }: DegradedBadgeProps) {
  const meta = KIND_META[kind];
  const tip = reason ?? meta.defaultReason;
  return (
    <span
      className={meta.className}
      data-kind={kind}
      role="status"
      title={tip}
      aria-label={`${meta.label}：${tip}`}
    >
      {meta.label}
    </span>
  );
}
