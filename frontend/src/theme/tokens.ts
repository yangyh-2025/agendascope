/**
 * AgendaScope 观澜设计 token：全站唯一的视觉规范来源。
 * 色值/间距/字号在此集中管理，CSS 变量由 applyCssVariables 注入 :root。
 */
export const colors = {
  /** 藏蓝：全局深色底 */
  navy: "#0B2A5B",
  /** 更深的底色层次（侧栏、输入框底） */
  navyDeep: "#071E42",
  /** 面板/卡片底 */
  navyPanel: "#10336E",
  /** 主蓝：主按钮、链接、导航高亮 */
  blue: "#1D4E9E",
  /** 主蓝悬停/亮阶 */
  blueBright: "#3D74C4",
  /** 中国红：预警、失败状态、关键高亮 */
  red: "#C8102E",
  /** 中国红暗阶（标签底色） */
  redDeep: "#7A0A1E",
  /** 主文字 */
  textPrimary: "#F0F4FA",
  /** 次级文字 */
  textSecondary: "#9DB2D0",
  /** 弱化文字/占位符 */
  textMuted: "#647C9E",
  /** 边框/分割线 */
  border: "#274B84",
  /** 成功/健康 */
  success: "#2FA96B",
  /** 降级/警告 */
  warning: "#D9A02B",
} as const;

export const spacing = {
  xs: "4px",
  sm: "8px",
  md: "16px",
  lg: "24px",
  xl: "32px",
  xxl: "48px",
} as const;

export const fontSize = {
  xs: "12px",
  sm: "13px",
  md: "14px",
  lg: "16px",
  xl: "20px",
  xxl: "24px",
  title: "32px",
} as const;

export const radius = {
  sm: "4px",
  md: "8px",
  lg: "12px",
} as const;

export const tokens = { colors, spacing, fontSize, radius } as const;

export type Tokens = typeof tokens;

/** 把 token 注入为 CSS 变量（--as-color-navy 等），保证 ts 与 css 单一来源。 */
export function applyCssVariables(root: HTMLElement = document.documentElement): void {
  for (const [group, values] of Object.entries(tokens)) {
    for (const [key, value] of Object.entries(values)) {
      root.style.setProperty(`--as-${group}-${key}`, value);
    }
  }
}
