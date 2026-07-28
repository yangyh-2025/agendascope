/** 议程时间线粒度逻辑（T4.8）：>90 天自动降周粒度。 */
export type RequestedGranularity = "hour" | "day";
export type EffectiveGranularity = "hour" | "day" | "week";

export const WEEK_DOWNGRADE_DAYS = 90;

/**
 * 计算实际请求粒度：时间窗超过 90 天时强制降为 week（1h/1d 数据量过大），
 * 否则按用户选择的 1h/1d。
 */
export function effectiveGranularity(
  days: number,
  requested: RequestedGranularity,
): EffectiveGranularity {
  if (days > WEEK_DOWNGRADE_DAYS) return "week";
  return requested;
}

/**
 * 峰值打标：返回各序列全局最大点的索引集合（每序列一个）。
 * 用于在堆叠面积图上自动标注峰值。
 */
export function peakIndex(values: number[]): number {
  if (values.length === 0) return -1;
  let best = 0;
  for (let i = 1; i < values.length; i += 1) {
    if (values[i] > values[best]) best = i;
  }
  return best;
}
