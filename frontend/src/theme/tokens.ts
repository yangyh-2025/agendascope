/**
 * AgendaScope 观澜设计 token:全站唯一的视觉规范来源。
 * 色值/间距/字号在此集中管理,CSS 变量由 applyCssVariables 注入 :root。
 *
 * 主题:亮色系"公安蓝 + 中国红"
 *  - 主色:公安蓝(主按钮、链接、激活导航、主图表)
 *  - 点缀:中国红(预警、激活高亮、危险操作),少量使用
 *  - 背景:纯白 + 浅灰分层,卡片浮在浅灰背景上
 *
 * 兼容性说明:旧变量名(navy/navyDeep/navyPanel/blue/blueBright/red/redDeep)
 * 全部保留为 alias,值映射到新亮色规范,旧 CSS 无需重命名即可生效。
 */
export const colors = {
  /* ============ 新规范:亮色系 ============ */
  /** 主色:公安蓝 — 主按钮/链接/激活导航/主要图表 */
  primary: "#1A4FA0",
  /** 主色 hover */
  primaryHover: "#2B63C4",
  /** 主色按下/深阶 */
  primaryActive: "#0F3D8A",
  /** 主色极浅背景(高亮区/选中底) */
  primarySoft: "#E8F0FB",

  /** 点缀:中国红 — 预警/危险/激活高亮 */
  accent: "#C8102E",
  /** 红色 hover */
  accentHover: "#A00D25",
  /** 红色极浅背景(告警卡片) */
  accentSoft: "#FBEAEC",

  /** 全局主背景(纯白) */
  bg: "#FFFFFF",
  /** 副背景(浅灰,主内容区底色) */
  bgSubtle: "#F5F7FA",
  /** 卡片/面板背景(纯白) */
  bgPanel: "#FFFFFF",
  /** 列表行 hover 背景 */
  bgHover: "#F0F4FA",

  /** 主文字(接近黑但带蓝调) */
  textPrimary: "#1F2D3D",
  /** 次级文字 */
  textSecondary: "#5E6D82",
  /** 弱化文字/占位符 */
  textMuted: "#9AA8BB",
  /** 反色文字(主色按钮/深色块上) */
  textInverse: "#FFFFFF",

  /** 浅边框/分割线 */
  border: "#E4E9F2",
  /** 强调边框 */
  borderStrong: "#C9D4E5",

  /** 成功/健康 */
  success: "#2FA96B",
  successSoft: "#E5F5EE",
  /** 降级/警告 */
  warning: "#D9A02B",
  warningSoft: "#FBF3E0",
  /** 危险(同 accent) */
  danger: "#C8102E",
  dangerSoft: "#FBEAEC",
  /** 信息(同 primary) */
  info: "#1A4FA0",
  infoSoft: "#E8F0FB",

  /* ============ 旧变量别名(兼容历史 CSS,不改名只改值) ============ */
  /** @deprecated 旧名,等同 bgSubtle — 原深色全局底,现改为浅灰 */
  navy: "#F5F7FA",
  /** @deprecated 旧名,等同 bgPanel — 原 sidebar 底,现改为白 */
  navyDeep: "#FFFFFF",
  /** @deprecated 旧名,等同 bgPanel — 原卡片/topbar 底,现改为白 */
  navyPanel: "#FFFFFF",
  /** @deprecated 旧名,等同 primary */
  blue: "#1A4FA0",
  /** @deprecated 旧名,等同 primaryHover */
  blueBright: "#2B63C4",
  /** @deprecated 旧名,等同 accent */
  red: "#C8102E",
  /** @deprecated 旧名,等同 accentHover */
  redDeep: "#A00D25",
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
  xl: "18px",
  xxl: "22px",
  title: "28px",
  display: "36px",
} as const;

export const fontWeight = {
  regular: "400",
  medium: "500",
  semibold: "600",
  bold: "700",
} as const;

export const radius = {
  sm: "6px",
  md: "10px",
  lg: "14px",
  xl: "20px",
} as const;

export const shadow = {
  xs: "0 1px 2px rgba(15, 61, 138, 0.04)",
  sm: "0 2px 8px rgba(15, 61, 138, 0.06)",
  md: "0 4px 16px rgba(15, 61, 138, 0.08)",
  lg: "0 8px 32px rgba(15, 61, 138, 0.12)",
  card: "0 1px 2px rgba(15, 61, 138, 0.04), 0 4px 12px rgba(15, 61, 138, 0.06)",
  cardHover:
    "0 4px 8px rgba(15, 61, 138, 0.06), 0 12px 32px rgba(15, 61, 138, 0.12)",
} as const;

export const transition = {
  fast: "all 0.15s ease",
  base: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
  slow: "all 0.4s cubic-bezier(0.4, 0, 0.2, 1)",
} as const;

/** ECharts 图表统一色板(浅色底数据驾驶舱风) */
export const chartPalette = [
  "#1A4FA0",
  "#C8102E",
  "#2FA96B",
  "#D9A02B",
  "#6B7FFF",
  "#F59E0B",
  "#10B981",
  "#8B5CF6",
] as const;

export const tokens = {
  colors,
  spacing,
  fontSize,
  fontWeight,
  radius,
  shadow,
  transition,
} as const;

export type Tokens = typeof tokens;

/** 把 token 注入为 CSS 变量(--as-color-navy 等),保证 ts 与 css 单一来源。 */
export function applyCssVariables(root: HTMLElement = document.documentElement): void {
  for (const [group, values] of Object.entries(tokens)) {
    for (const [key, value] of Object.entries(values)) {
      root.style.setProperty(`--as-${group}-${key}`, value);
    }
  }
}
