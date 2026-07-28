import { describe, expect, it } from "vitest";
import { effectiveGranularity, peakIndex } from "./timeline";

describe("时间线粒度与峰值逻辑", () => {
  it("≤90 天保持用户选择的 1h/1d 粒度", () => {
    expect(effectiveGranularity(7, "hour")).toBe("hour");
    expect(effectiveGranularity(30, "day")).toBe("day");
    expect(effectiveGranularity(90, "hour")).toBe("hour");
  });

  it(">90 天自动降为周粒度（无论用户选什么）", () => {
    expect(effectiveGranularity(91, "hour")).toBe("week");
    expect(effectiveGranularity(180, "day")).toBe("week");
    expect(effectiveGranularity(365, "hour")).toBe("week");
  });

  it("peakIndex 返回全局最大值下标", () => {
    expect(peakIndex([1, 5, 3, 5, 2])).toBe(1);
    expect(peakIndex([7])).toBe(0);
    expect(peakIndex([])).toBe(-1);
  });
});
