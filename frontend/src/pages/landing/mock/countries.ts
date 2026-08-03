/**
 * 108 国坐标数据(静态快照,仅用于 Landing 页 3D 地球光点)。
 * 来源: backend/app/core/countries.py 单一事实源 + 各国首都/质心经纬度。
 * 仅 Landing 使用,不参与业务逻辑;业务侧仍以 backend 为准。
 */

export interface CountryPoint {
  code: string;   // ISO 3166-1 alpha-2
  nameZh: string;
  lat: number;
  lng: number;
  region: string;
}

export const COUNTRIES: CountryPoint[] = [
  // ---- 东亚 ----
  { code: "CN", nameZh: "中国", lat: 35.0, lng: 105.0, region: "东亚" },
  { code: "JP", nameZh: "日本", lat: 36.2, lng: 138.25, region: "东亚" },
  { code: "KR", nameZh: "韩国", lat: 36.5, lng: 127.9, region: "东亚" },
  // ---- 东南亚 ----
  { code: "ID", nameZh: "印度尼西亚", lat: -2.5, lng: 118.0, region: "东南亚" },
  { code: "MY", nameZh: "马来西亚", lat: 4.2, lng: 102.0, region: "东南亚" },
  { code: "SG", nameZh: "新加坡", lat: 1.35, lng: 103.82, region: "东南亚" },
  { code: "TH", nameZh: "泰国", lat: 15.87, lng: 100.99, region: "东南亚" },
  { code: "VN", nameZh: "越南", lat: 14.06, lng: 108.28, region: "东南亚" },
  { code: "PH", nameZh: "菲律宾", lat: 12.88, lng: 121.77, region: "东南亚" },
  { code: "MM", nameZh: "缅甸", lat: 21.92, lng: 95.96, region: "东南亚" },
  { code: "KH", nameZh: "柬埔寨", lat: 12.56, lng: 104.99, region: "东南亚" },
  { code: "LA", nameZh: "老挝", lat: 19.85, lng: 102.5, region: "东南亚" },
  { code: "BN", nameZh: "文莱", lat: 4.53, lng: 114.73, region: "东南亚" },
  // ---- 南亚 ----
  { code: "IN", nameZh: "印度", lat: 21.0, lng: 78.0, region: "南亚" },
  { code: "PK", nameZh: "巴基斯坦", lat: 30.38, lng: 69.35, region: "南亚" },
  { code: "BD", nameZh: "孟加拉国", lat: 23.68, lng: 90.36, region: "南亚" },
  { code: "LK", nameZh: "斯里兰卡", lat: 7.87, lng: 80.77, region: "南亚" },
  { code: "NP", nameZh: "尼泊尔", lat: 28.39, lng: 84.12, region: "南亚" },
  { code: "AF", nameZh: "阿富汗", lat: 33.94, lng: 67.71, region: "南亚" },
  // ---- 中东 ----
  { code: "SA", nameZh: "沙特阿拉伯", lat: 23.89, lng: 45.08, region: "中东" },
  { code: "AE", nameZh: "阿联酋", lat: 23.42, lng: 53.85, region: "中东" },
  { code: "QA", nameZh: "卡塔尔", lat: 25.35, lng: 51.18, region: "中东" },
  { code: "IR", nameZh: "伊朗", lat: 32.43, lng: 53.69, region: "中东" },
  { code: "IL", nameZh: "以色列", lat: 31.05, lng: 34.85, region: "中东" },
  { code: "TR", nameZh: "土耳其", lat: 38.96, lng: 35.24, region: "中东" },
  { code: "KW", nameZh: "科威特", lat: 29.31, lng: 47.48, region: "中东" },
  { code: "JO", nameZh: "约旦", lat: 31.24, lng: 36.51, region: "中东" },
  { code: "LB", nameZh: "黎巴嫩", lat: 33.85, lng: 35.86, region: "中东" },
  { code: "IQ", nameZh: "伊拉克", lat: 33.22, lng: 43.68, region: "中东" },
  { code: "SY", nameZh: "叙利亚", lat: 34.8, lng: 38.99, region: "中东" },
  { code: "YE", nameZh: "也门", lat: 15.55, lng: 48.52, region: "中东" },
  { code: "BH", nameZh: "巴林", lat: 26.07, lng: 50.55, region: "中东" },
  { code: "OM", nameZh: "阿曼", lat: 21.47, lng: 55.98, region: "中东" },
  { code: "PS", nameZh: "巴勒斯坦", lat: 31.9, lng: 35.2, region: "中东" },
  // ---- 欧洲 ----
  { code: "GB", nameZh: "英国", lat: 54.0, lng: -2.0, region: "欧洲" },
  { code: "DE", nameZh: "德国", lat: 51.17, lng: 10.45, region: "欧洲" },
  { code: "FR", nameZh: "法国", lat: 46.6, lng: 2.5, region: "欧洲" },
  { code: "IT", nameZh: "意大利", lat: 42.83, lng: 12.83, region: "欧洲" },
  { code: "ES", nameZh: "西班牙", lat: 40.3, lng: -3.7, region: "欧洲" },
  { code: "RU", nameZh: "俄罗斯", lat: 61.5, lng: 90.0, region: "欧洲" },
  { code: "PL", nameZh: "波兰", lat: 51.92, lng: 19.14, region: "欧洲" },
  { code: "SE", nameZh: "瑞典", lat: 62.0, lng: 15.0, region: "欧洲" },
  { code: "NO", nameZh: "挪威", lat: 64.0, lng: 11.0, region: "欧洲" },
  { code: "CH", nameZh: "瑞士", lat: 46.8, lng: 8.23, region: "欧洲" },
  { code: "NL", nameZh: "荷兰", lat: 52.2, lng: 5.3, region: "欧洲" },
  { code: "BE", nameZh: "比利时", lat: 50.5, lng: 4.7, region: "欧洲" },
  { code: "GR", nameZh: "希腊", lat: 39.0, lng: 22.0, region: "欧洲" },
  { code: "PT", nameZh: "葡萄牙", lat: 39.6, lng: -8.0, region: "欧洲" },
  { code: "FI", nameZh: "芬兰", lat: 64.0, lng: 26.0, region: "欧洲" },
  { code: "DK", nameZh: "丹麦", lat: 56.0, lng: 10.0, region: "欧洲" },
  { code: "CZ", nameZh: "捷克", lat: 49.8, lng: 15.5, region: "欧洲" },
  { code: "AT", nameZh: "奥地利", lat: 47.6, lng: 14.14, region: "欧洲" },
  { code: "IE", nameZh: "爱尔兰", lat: 53.18, lng: -8.24, region: "欧洲" },
  { code: "UA", nameZh: "乌克兰", lat: 49.0, lng: 32.0, region: "欧洲" },
  { code: "HU", nameZh: "匈牙利", lat: 47.16, lng: 19.5, region: "欧洲" },
  { code: "RO", nameZh: "罗马尼亚", lat: 45.94, lng: 25.0, region: "欧洲" },
  { code: "BG", nameZh: "保加利亚", lat: 42.7, lng: 25.5, region: "欧洲" },
  { code: "SK", nameZh: "斯洛伐克", lat: 48.7, lng: 19.6, region: "欧洲" },
  // ---- 北美 ----
  { code: "US", nameZh: "美国", lat: 39.8, lng: -98.6, region: "北美" },
  { code: "CA", nameZh: "加拿大", lat: 56.13, lng: -106.35, region: "北美" },
  // ---- 拉美 ----
  { code: "BR", nameZh: "巴西", lat: -10.0, lng: -55.0, region: "拉美" },
  { code: "MX", nameZh: "墨西哥", lat: 23.63, lng: -102.55, region: "拉美" },
  { code: "AR", nameZh: "阿根廷", lat: -34.0, lng: -64.0, region: "拉美" },
  { code: "CL", nameZh: "智利", lat: -31.76, lng: -71.0, region: "拉美" },
  { code: "CO", nameZh: "哥伦比亚", lat: 4.57, lng: -74.1, region: "拉美" },
  { code: "PE", nameZh: "秘鲁", lat: -9.19, lng: -75.02, region: "拉美" },
  { code: "UY", nameZh: "乌拉圭", lat: -32.52, lng: -55.77, region: "拉美" },
  { code: "BO", nameZh: "玻利维亚", lat: -16.29, lng: -63.59, region: "拉美" },
  { code: "EC", nameZh: "厄瓜多尔", lat: -1.83, lng: -78.18, region: "拉美" },
  { code: "VE", nameZh: "委内瑞拉", lat: 6.42, lng: -66.59, region: "拉美" },
  { code: "PY", nameZh: "巴拉圭", lat: -23.44, lng: -58.44, region: "拉美" },
  { code: "CU", nameZh: "古巴", lat: 21.52, lng: -77.78, region: "拉美" },
  { code: "DO", nameZh: "多米尼加", lat: 18.74, lng: -70.16, region: "拉美" },
  // ---- 大洋洲 ----
  { code: "AU", nameZh: "澳大利亚", lat: -25.0, lng: 135.0, region: "大洋洲" },
  { code: "NZ", nameZh: "新西兰", lat: -41.84, lng: 172.76, region: "大洋洲" },
  { code: "FJ", nameZh: "斐济", lat: -17.71, lng: 178.07, region: "大洋洲" },
  // ---- 非洲 ----
  { code: "ZA", nameZh: "南非", lat: -29.0, lng: 24.0, region: "非洲" },
  { code: "EG", nameZh: "埃及", lat: 26.82, lng: 30.8, region: "非洲" },
  { code: "NG", nameZh: "尼日利亚", lat: 9.08, lng: 8.68, region: "非洲" },
  { code: "KE", nameZh: "肯尼亚", lat: -0.02, lng: 37.91, region: "非洲" },
  { code: "ET", nameZh: "埃塞俄比亚", lat: 9.15, lng: 40.49, region: "非洲" },
  { code: "MA", nameZh: "摩洛哥", lat: 31.79, lng: -7.09, region: "非洲" },
  { code: "GH", nameZh: "加纳", lat: 7.95, lng: -1.02, region: "非洲" },
  { code: "TZ", nameZh: "坦桑尼亚", lat: -6.37, lng: 34.89, region: "非洲" },
  { code: "UG", nameZh: "乌干达", lat: 1.37, lng: 32.29, region: "非洲" },
  { code: "DZ", nameZh: "阿尔及利亚", lat: 28.03, lng: 1.66, region: "非洲" },
  { code: "TN", nameZh: "突尼斯", lat: 33.89, lng: 9.56, region: "非洲" },
  { code: "LY", nameZh: "利比亚", lat: 26.34, lng: 17.23, region: "非洲" },
  { code: "RW", nameZh: "卢旺达", lat: -1.94, lng: 29.87, region: "非洲" },
  { code: "SN", nameZh: "塞内加尔", lat: 14.5, lng: -14.45, region: "非洲" },
  { code: "CI", nameZh: "科特迪瓦", lat: 7.54, lng: -5.55, region: "非洲" },
  { code: "CM", nameZh: "喀麦隆", lat: 5.7, lng: 12.7, region: "非洲" },
  { code: "AO", nameZh: "安哥拉", lat: -11.2, lng: 17.87, region: "非洲" },
  { code: "MZ", nameZh: "莫桑比克", lat: -18.67, lng: 35.53, region: "非洲" },
  { code: "ZM", nameZh: "赞比亚", lat: -13.13, lng: 27.85, region: "非洲" },
  { code: "ZW", nameZh: "津巴布韦", lat: -19.02, lng: 29.15, region: "非洲" },
  { code: "BW", nameZh: "博茨瓦纳", lat: -22.33, lng: 24.68, region: "非洲" },
  { code: "GA", nameZh: "加蓬", lat: -0.8, lng: 11.6, region: "非洲" },
  { code: "CD", nameZh: "刚果(金)", lat: -2.88, lng: 23.66, region: "非洲" },
  // ---- 中亚 ----
  { code: "KZ", nameZh: "哈萨克斯坦", lat: 48.02, lng: 66.92, region: "中亚" },
  { code: "UZ", nameZh: "乌兹别克斯坦", lat: 41.38, lng: 64.59, region: "中亚" },
  { code: "TM", nameZh: "土库曼斯坦", lat: 38.97, lng: 59.56, region: "中亚" },
  { code: "KG", nameZh: "吉尔吉斯斯坦", lat: 41.2, lng: 74.77, region: "中亚" },
  { code: "TJ", nameZh: "塔吉克斯坦", lat: 38.86, lng: 71.28, region: "中亚" },
  { code: "AZ", nameZh: "阿塞拜疆", lat: 40.14, lng: 47.58, region: "中亚" },
  { code: "GE", nameZh: "格鲁吉亚", lat: 42.32, lng: 43.37, region: "中亚" },
  { code: "AM", nameZh: "亚美尼亚", lat: 40.07, lng: 45.04, region: "中亚" },
  { code: "BY", nameZh: "白俄罗斯", lat: 53.71, lng: 27.95, region: "中亚" },
];
