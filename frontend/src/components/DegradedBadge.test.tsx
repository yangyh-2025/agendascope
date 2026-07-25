import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DegradedBadge, { type DegradedKind } from "./DegradedBadge";

describe("DegradedBadge 降级标注", () => {
  it.each<[DegradedKind, string]>([
    ["llm_degraded", "LLM 降级中"],
    ["cluster_degraded", "聚类降级中"],
    ["translation_degraded", "跨语言匹配暂不可用"],
    ["origin_needs_review", "首发源待核实"],
    ["snapshot_outdated", "快照校正中"],
    ["coverage_low", "数据覆盖不足"],
  ])("kind=%s 渲染为「%s」", (kind, label) => {
    render(<DegradedBadge kind={kind} />);
    const el = screen.getByText(label);
    expect(el).toBeInTheDocument();
    expect(el).toHaveAttribute("data-kind", kind);
  });

  it("默认 tooltip 使用类型说明，自定义 reason 覆盖默认", () => {
    const { rerender } = render(<DegradedBadge kind="llm_degraded" />);
    expect(screen.getByText("LLM 降级中")).toHaveAttribute(
      "title",
      expect.stringContaining("LLM 不可用"),
    );

    rerender(<DegradedBadge kind="llm_degraded" reason="GPU 显存不足" />);
    expect(screen.getByText("LLM 降级中")).toHaveAttribute("title", "GPU 显存不足");
  });

  it("红色中国红 #C8102E 不允许出现在 badge className（视觉规范）", () => {
    const { container } = render(<DegradedBadge kind="llm_degraded" />);
    const badge = container.querySelector(".degraded-badge");
    expect(badge?.className).not.toMatch(/red|danger/i);
  });
});
