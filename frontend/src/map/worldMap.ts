/**
 * 离线世界地图（T4.6 前置修复）：
 * - 构建期把 world-atlas 的 TopoJSON（countries-50m；110m 缺少新加坡等小国土要素）转成 GeoJSON 并 echarts.registerMap('world')
 * - GeoJSON 随前端包离线打包，运行时绝不从公网拉取地图数据
 * - 提供 ISO-3166 alpha2 → 地图要素英文名 映射与各国外接框中心（流向动画取点用）
 *
 * 合规修补（chinaCompliance.ts）：
 * - 台湾并入中国（一个中国原则）
 * - 藏南地区、阿克赛钦 以追加 Polygon 的方式并入中国要素（world-atlas 数据源
 *   按"麦克马洪线"/"约翰逊线"错误划给印度，此处按中国主张线归并）
 */
import * as echarts from "echarts";
import { feature } from "topojson-client";
import type { Topology } from "topojson-specification";
import countries50m from "world-atlas/countries-50m.json";
import { CHINA_COMPLIANCE_PATCHES } from "./chinaCompliance";

export const WORLD_MAP_NAME = "world";

/** 后端目标国（含地图接口可能出现的额外采集国）→ world-atlas 要素英文名。 */
export const ISO2_TO_MAP_NAME: Record<string, string> = {
  CN: "China",
  US: "United States of America",
  GB: "United Kingdom",
  FR: "France",
  DE: "Germany",
  RU: "Russia",
  JP: "Japan",
  KR: "South Korea",
  IN: "India",
  AU: "Australia",
  CA: "Canada",
  BR: "Brazil",
  MX: "Mexico",
  AR: "Argentina",
  ZA: "South Africa",
  EG: "Egypt",
  NG: "Nigeria",
  KE: "Kenya",
  SA: "Saudi Arabia",
  AE: "United Arab Emirates",
  IR: "Iran",
  TR: "Turkey",
  IL: "Israel",
  PK: "Pakistan",
  ID: "Indonesia",
  MY: "Malaysia",
  SG: "Singapore",
  TH: "Thailand",
  VN: "Vietnam",
  PH: "Philippines",
  QA: "Qatar",
  IT: "Italy",
  ES: "Spain",
  PL: "Poland",
  SE: "Sweden",
  NO: "Norway",
  CH: "Switzerland",
  NL: "Netherlands",
  BE: "Belgium",
  MM: "Myanmar",
  KH: "Cambodia",
  LK: "Sri Lanka",
  NP: "Nepal",
  KW: "Kuwait",
  JO: "Jordan",
  LB: "Lebanon",
  CL: "Chile",
  CO: "Colombia",
  PE: "Peru",
  NZ: "New Zealand",
  MA: "Morocco",
  GH: "Ghana",
  TZ: "Tanzania",
  UG: "Uganda",
  KZ: "Kazakhstan",
  LA: "Laos",
  BN: "Brunei",
  AF: "Afghanistan",
  IQ: "Iraq",
  SY: "Syria",
  YE: "Yemen",
  BH: "Bahrain",
  OM: "Oman",
  PS: "Palestine",
  GR: "Greece",
  PT: "Portugal",
  FI: "Finland",
  DK: "Denmark",
  CZ: "Czechia",
  AT: "Austria",
  IE: "Ireland",
  UA: "Ukraine",
  HU: "Hungary",
  RO: "Romania",
  BG: "Bulgaria",
  SK: "Slovakia",
  UY: "Uruguay",
  BO: "Bolivia",
  EC: "Ecuador",
  VE: "Venezuela",
  PY: "Paraguay",
  CU: "Cuba",
  DO: "Dominican Rep.",
  FJ: "Fiji",
  DZ: "Algeria",
  TN: "Tunisia",
  LY: "Libya",
  RW: "Rwanda",
  SN: "Senegal",
  CI: "Côte d'Ivoire",
  CM: "Cameroon",
  AO: "Angola",
  MZ: "Mozambique",
  ZM: "Zambia",
  ZW: "Zimbabwe",
  BW: "Botswana",
  GA: "Gabon",
  CD: "Dem. Rep. Congo",
  UZ: "Uzbekistan",
  TM: "Turkmenistan",
  KG: "Kyrgyzstan",
  TJ: "Tajikistan",
  AZ: "Azerbaijan",
  GE: "Georgia",
  AM: "Armenia",
  BY: "Belarus",
};

interface GeoFeature {
  type: string;
  id?: string;
  properties?: { name?: string };
  geometry?: {
    type: string;
    coordinates: unknown;
  };
}

export interface WorldGeoJson {
  type: "FeatureCollection";
  features: GeoFeature[];
}

let registered: WorldGeoJson | null = null;
const centroidByName = new Map<string, [number, number]>();

/** ring 的鞋带公式面积（平方经纬度，带符号；取绝对值）。 */
function ringArea(ring: number[][]): number {
  let area = 0;
  for (let i = 0; i < ring.length; i++) {
    const [lng1, lat1] = ring[i];
    const [lng2, lat2] = ring[(i + 1) % ring.length];
    area += lng1 * lat2 - lng2 * lat1;
  }
  return Math.abs(area / 2);
}

/** ring 的质心（面积加权）。 */
function ringCentroid(ring: number[][]): [number, number] {
  let cx = 0;
  let cy = 0;
  let area = 0;
  for (let i = 0; i < ring.length; i++) {
    const [lng1, lat1] = ring[i];
    const [lng2, lat2] = ring[(i + 1) % ring.length];
    const cross = lng1 * lat2 - lng2 * lat1;
    cx += (lng1 + lng2) * cross;
    cy += (lat1 + lat2) * cross;
    area += cross;
  }
  if (Math.abs(area) < 1e-9) return [0, 0];
  return [cx / (3 * area), cy / (3 * area)];
}

/**
 * 计算 Polygon/MultiPolygon 的面积加权质心（真实地理中心）。
 * 相比 bboxCenter（外接框中心），对俄罗斯/中国/美国等大国不偏移
 * （外接框中心会偏向北极或海洋）。空坐标返回 null。
 */
function polygonCentroid(coords: unknown): [number, number] | null {
  // Polygon: [[[lng,lat],...]]（coords[0] 是 ring，ring[0] 是点）→ 包成 [coords]
  // MultiPolygon: [[[[lng,lat],...],...]]（coords[0] 是 poly）→ 直接用
  const isSinglePolygon = Array.isArray(coords)
    && Array.isArray(coords[0])
    && Array.isArray(coords[0][0])
    && typeof coords[0][0][0] === "number";
  const polys: unknown[] = isSinglePolygon ? [coords] : (coords as unknown[]);

  let totalArea = 0;
  let cx = 0;
  let cy = 0;
  for (const poly of polys) {
    if (!Array.isArray(poly) || poly.length === 0) continue;
    // 取最大面积 ring（外环）：部分国家（HU/BG/AT 等）首 ring 是内环（退化），
    // 固定 poly[0] 会算出 null/错误质心——取面积最大者最稳。
    let bestRing: number[][] | null = null;
    let bestArea = 0;
    for (const ring of poly) {
      if (!Array.isArray(ring) || !Array.isArray(ring[0])) continue;
      const a = ringArea(ring as number[][]);
      if (a > bestArea) {
        bestArea = a;
        bestRing = ring as number[][];
      }
    }
    if (!bestRing || bestArea <= 0) continue;
    const [rcx, rcy] = ringCentroid(bestRing);
    totalArea += bestArea;
    cx += rcx * bestArea;
    cy += rcy * bestArea;
  }
  if (totalArea <= 0) return null;
  return [cx / totalArea, cy / totalArea];
}

/**
 * 计算一个 Polygon(一组 ring)的经度范围。
 * coords 结构: [ring, ring, ...],ring = [[lng, lat], ...]
 */
function polygonLngRange(coords: unknown): [number, number] | null {
  let minLng = Infinity;
  let maxLng = -Infinity;
  const walk = (node: unknown): void => {
    if (!Array.isArray(node)) return;
    if (typeof node[0] === "number" && typeof node[1] === "number") {
      const [lng] = node as [number, number];
      if (lng < minLng) minLng = lng;
      if (lng > maxLng) maxLng = lng;
      return;
    }
    for (const child of node) walk(child);
  };
  walk(coords);
  if (!Number.isFinite(minLng)) return null;
  return [minLng, maxLng];
}

/**
 * 把跨 180° 反经线的多边形"挪到同一侧":
 * 俄罗斯/斐济/南极洲等国家的 MultiPolygon 真实跨越 180° 经线
 * (楚科奇半岛、阿留申群岛等)。GeoJSON 里这些点用 -179.x 表示,
 * ECharts 渲染时会从 +179 拉到 -179,形成横贯地图的长条。
 *
 * 修法:对每个坐标点,如果经度 < 0 则 +360,把 -179 → +181。
 * 这样所有点都落在东经范围,ECharts 就能正确绘制连续多边形。
 *
 * 注意:这只是显示层修补,不修改原始数据。ECharts 内部会把 181° 显示在
 * 地图 181° 的位置(世界地图本身覆盖 -180 ~ 180,超过 180 的部分会延伸到
 * 地图右侧边缘),这正是我们想要的效果:楚科奇半岛出现在俄罗斯右侧,
 * 而不是被拉到地图最左。
 */
function unwrapAntimeridianCoords(coords: unknown): unknown {
  if (!Array.isArray(coords)) return coords;
  if (typeof coords[0] === "number" && typeof coords[1] === "number") {
    const [lng, lat] = coords as [number, number];
    // 西经 → 东经 360° 平移
    return [lng < 0 ? lng + 360 : lng, lat];
  }
  return coords.map(unwrapAntimeridianCoords);
}

/**
 * 修复 world-atlas GeoJSON 的 180° 反经线问题:
 *
 * 1. 南极洲:不是业务关注对象,且它跨所有经度,任何处理都会出问题,直接移除
 *
 * 2. 跨 180° 的多边形(俄罗斯楚科奇、美国阿留申等):ECharts 会以为从 +179 直接
 *    拉到 -179,形成"飞线长条"。修法:把西经坐标平移到 360° 坐标系(-179 → +181),
 *    让多边形连续
 */
function clipAntimeridianFragments(geo: WorldGeoJson): WorldGeoJson {
  // 南极洲既跨 180° 又不是业务关注对象,直接整体移除,避免长条 + 偏移两类问题
  geo.features = geo.features.filter((f) => f.properties?.name !== "Antarctica");

  for (const f of geo.features) {
    const geom = f.geometry;
    if (!geom) continue;

    if (geom.type === "MultiPolygon") {
      const polys = geom.coordinates as unknown[];
      geom.coordinates = polys.map((poly) => {
        const range = polygonLngRange(poly);
        if (!range) return poly;
        const [minLng, maxLng] = range;
        // 只处理真正横跨 180° 的多边形
        const crossesAntimeridian = minLng < -170 && maxLng > 170;
        if (crossesAntimeridian) {
          return unwrapAntimeridianCoords(poly) as never;
        }
        return poly;
      }) as never;
    } else if (geom.type === "Polygon") {
      const range = polygonLngRange(geom.coordinates);
      if (range) {
        const [minLng, maxLng] = range;
        const crossesAntimeridian = minLng < -170 && maxLng > 170;
        if (crossesAntimeridian) {
          geom.coordinates = unwrapAntimeridianCoords(geom.coordinates) as never;
        }
      }
    }
  }
  return geo;
}

/**
 * 注册世界地图(幂等)。返回注册用的 GeoJSON,供测试与调试断言。
 * 后续 echarts 实例只需使用 map: 'world' 即可渲染,无需再传 geoJSON。
 *
 * 在注册前会调用 clipAntimeridianFragments 修掉俄罗斯等跨国 180° 经线国家
 * 的"溢出碎块",避免渲染时出现孤立的远程长条。
 */
/**
 * 把 world-atlas 中的 Taiwan 要素并入 China（一个中国原则，合规化）。
 * 做法：把 Taiwan 的 geometry 合并进 China（若 China 是 Polygon 则升级为
 * MultiPolygon 追加），并删除独立 Taiwan 要素——地图上台湾与中国统一渲染。
 */
function mergeTaiwanIntoChina(geo: WorldGeoJson): WorldGeoJson {
  const taiwanIdx = geo.features.findIndex((f) => f.properties?.name === "Taiwan");
  if (taiwanIdx < 0) return geo;
  const taiwan = geo.features[taiwanIdx];
  const china = geo.features.find((f) => f.properties?.name === "China");
  if (!china || !taiwan.geometry) return geo;

  const taiwanGeom = taiwan.geometry;
  // 归一化为 MultiPolygon 坐标数组（[[ring...], [ring...]])
  const taiwanPolys: unknown[] =
    taiwanGeom.type === "MultiPolygon"
      ? (taiwanGeom.coordinates as unknown[])
      : [taiwanGeom.coordinates];

  if (china.geometry) {
    if (china.geometry.type === "MultiPolygon") {
      (china.geometry.coordinates as unknown[]).push(...taiwanPolys);
    } else if (china.geometry.type === "Polygon") {
      china.geometry = {
        type: "MultiPolygon",
        coordinates: [china.geometry.coordinates, ...taiwanPolys] as never,
      };
    }
  }
  geo.features.splice(taiwanIdx, 1);
  return geo;
}

/** 把藏南、阿克赛钦等中国主张地区以追加 Polygon 的方式并入 China 要素。
 * world-atlas / Natural Earth 数据源按"麦克马洪线"/"约翰逊线"把这些地区错误
 * 划给印度，合规要求按中国主张线归并。叠加补丁 Polygon 是最小侵入式做法——
 * 不修改 India 的现有要素（重叠区域 China 覆盖绘制时按 z-order 覆盖）。
 */
function mergeChinaCompliancePatches(geo: WorldGeoJson): WorldGeoJson {
  const china = geo.features.find((f) => f.properties?.name === "China");
  if (!china) return geo;
  if (!china.geometry) {
    china.geometry = { type: "MultiPolygon", coordinates: [] as never };
  }
  if (china.geometry.type === "Polygon") {
    china.geometry = {
      type: "MultiPolygon",
      coordinates: [china.geometry.coordinates] as never,
    };
  }
  const coords = china.geometry.coordinates as unknown[];
  for (const patch of CHINA_COMPLIANCE_PATCHES) {
    // Polygon 结构: [ring, ring, ...],ring = [[lng, lat], ...]
    // 这里只添加外环(单 ring)
    coords.push([patch.polygon]);
  }
  return geo;
}

export function registerWorldMap(): WorldGeoJson {
  if (registered) return registered;
  const topo = countries50m as unknown as Topology;
  const geo = feature(
    topo,
    topo.objects.countries,
  ) as unknown as WorldGeoJson;
  mergeTaiwanIntoChina(geo);  // 台湾并入中国(合规)
  mergeChinaCompliancePatches(geo);  // 藏南/阿克赛钦归并中国(合规)
  clipAntimeridianFragments(geo);
  for (const f of geo.features) {
    const name = f.properties?.name;
    if (!name) continue;
    const center = polygonCentroid(f.geometry?.coordinates);
    if (center) centroidByName.set(name, center);
  }
  echarts.registerMap(WORLD_MAP_NAME, geo as never);
  registered = geo;
  return geo;
}

/** ISO alpha2 → 注册地图上的要素名；未收录返回 undefined。 */
export function mapNameOf(iso2: string): string | undefined {
  return ISO2_TO_MAP_NAME[iso2.toUpperCase()];
}

/**
 * 视觉锚点表(首都/最大城市经纬度)。
 *
 * 为什么需要:countryCenter 几何质心对跨 180°(RU)、带海外领地(FR/GB/NO)、
 * 多岛/大陆复合(US 阿拉斯加+夏威夷)等国家会严重偏移——RU 质心被楚科奇拉到
 * 白令海峡、US 被阿拉斯拉到北极圈、FR 被法属圭亚那拖到大西洋。
 * 传播地图上的"国家点"用户预期是政治中心而非几何中心,这里手工钉住。
 *
 * 未列入的国家走几何质心兜底(对小国/紧凑国家质心通常合理)。
 */
const VISUAL_ANCHOR: Record<string, [number, number]> = {
  // 大国/跨多边形国家(质心必偏)
  US: [-77.04, 38.90],    // 华盛顿
  CN: [116.41, 39.90],    // 北京
  RU: [37.62, 55.76],     // 莫斯科
  CA: [-75.70, 45.42],    // 渥太华
  AU: [149.13, -35.28],   // 堪培拉
  BR: [-47.93, -15.79],   // 巴西利亚
  IN: [77.21, 28.61],     // 新德里
  FR: [2.35, 48.86],      // 巴黎
  GB: [-0.13, 51.50],     // 伦敦
  NO: [10.75, 59.91],     // 奥斯陆
  NZ: [174.78, -41.29],   // 惠灵顿
  JP: [139.69, 35.69],    // 东京
  MX: [-99.13, 19.43],    // 墨西哥城
  AR: [-58.38, -34.60],   // 布宜诺斯艾利斯
  CL: [-70.67, -33.45],   // 圣地亚哥
  ID: [106.85, -6.21],    // 雅加达
  PH: [120.98, 14.60],    // 马尼拉
  // 欧洲
  DE: [13.40, 52.52],     // 柏林
  IT: [12.48, 41.89],     // 罗马
  ES: [-3.70, 40.42],     // 马德里
  PL: [21.01, 52.23],     // 华沙
  SE: [18.07, 59.33],     // 斯德哥尔摩
  CH: [7.45, 46.95],      // 伯尔尼
  NL: [4.90, 52.37],      // 阿姆斯特丹
  BE: [4.35, 50.85],      // 布鲁塞尔
  AT: [16.37, 48.21],     // 维也纳
  IE: [-6.26, 53.35],     // 都柏林
  UA: [30.52, 50.45],     // 基辅
  HU: [19.04, 47.50],     // 布达佩斯
  RO: [26.10, 44.44],     // 布加勒斯特
  BG: [23.32, 42.70],     // 索菲亚
  SK: [17.11, 48.15],     // 布拉迪斯拉发
  GR: [23.73, 37.98],     // 雅典
  PT: [-9.14, 38.72],     // 里斯本
  FI: [24.94, 60.17],     // 赫尔辛基
  DK: [12.57, 55.68],     // 哥本哈根
  CZ: [14.44, 50.08],     // 布拉格
  // 亚太
  KR: [126.98, 37.57],    // 首尔
  SG: [103.82, 1.35],     // 新加坡
  MY: [101.69, 3.14],     // 吉隆坡
  TH: [100.50, 13.76],    // 曼谷
  VN: [105.85, 21.03],    // 河内
  MM: [96.16, 16.84],     // 仰光(最大城市;首都是内比都,但仰光是舆论中心)
  KH: [104.92, 11.55],    // 金边
  LK: [79.86, 6.93],      // 科伦坡
  NP: [85.32, 27.71],     // 加德满都
  LA: [102.63, 17.97],    // 万象
  BN: [114.94, 4.90],     // 斯里巴加湾
  FJ: [178.45, -18.14],   // 苏瓦
  PK: [73.05, 33.69],     // 伊斯兰堡
  AF: [69.18, 34.53],     // 喀布尔
  KZ: [71.43, 51.17],     // 阿斯塔纳
  UZ: [69.24, 41.30],     // 塔什干
  TM: [58.38, 37.95],     // 阿什哈巴德
  KG: [74.57, 42.88],     // 比什凯克
  TJ: [68.78, 38.56],     // 杜尚别
  AZ: [49.87, 40.41],     // 巴库
  GE: [44.79, 41.72],     // 第比利斯
  AM: [44.51, 40.18],     // 埃里温
  BY: [27.56, 53.90],     // 明斯克
  // 中东
  SA: [46.68, 24.71],     // 利雅得
  AE: [54.37, 24.45],     // 阿布扎比
  IR: [51.39, 35.69],     // 德黑兰
  TR: [32.86, 39.93],     // 安卡拉
  IL: [35.21, 31.78],     // 耶路撒冷
  QA: [51.53, 25.29],     // 多哈
  KW: [47.98, 29.38],     // 科威特城
  JO: [35.93, 31.95],     // 安曼
  LB: [35.50, 33.89],     // 贝鲁特
  IQ: [44.36, 33.31],     // 巴格达
  SY: [36.29, 33.51],     // 大马士革
  YE: [44.21, 15.35],     // 萨那
  BH: [50.59, 26.23],     // 麦纳麦
  OM: [58.41, 23.59],     // 马斯喀特
  PS: [35.23, 31.90],     // 拉马拉
  // 非洲
  EG: [31.24, 30.04],     // 开罗
  NG: [7.49, 9.06],       // 阿布贾
  KE: [36.82, -1.29],     // 内罗毕
  ZA: [28.19, -25.75],    // 比勒陀利亚(行政首都)
  MA: [-6.83, 34.01],     // 拉巴特
  GH: [-0.19, 5.60],      // 阿克拉
  TZ: [35.75, -6.16],     // 多多马(首都;达累斯萨拉姆是经济中心)
  UG: [32.58, 0.35],      // 坎帕拉
  DZ: [3.06, 36.74],      // 阿尔及尔
  TN: [10.18, 36.80],     // 突尼斯市
  LY: [13.19, 32.89],     // 的黎波里
  RW: [30.06, -1.94],     // 基加利
  SN: [-17.44, 14.69],    // 达喀尔
  CI: [-4.02, 5.32],      // 阿比让(经济首都;亚穆苏克罗是政治首都)
  CM: [11.50, 3.85],      // 雅温得
  AO: [13.23, -8.81],     // 罗安达
  MZ: [32.57, -25.97],    // 马普托
  ZM: [28.28, -15.41],    // 卢萨卡
  ZW: [31.05, -17.82],    // 哈拉雷
  BW: [25.91, -24.65],    // 哈博罗内
  GA: [9.45, 0.39],       // 利伯维尔
  CD: [15.31, -4.32],     // 金沙萨
  // 美洲
  CO: [-74.08, 4.61],     // 波哥大
  PE: [-77.04, -12.05],   // 利马
  UY: [-56.19, -34.90],   // 蒙得维的亚
  BO: [-68.12, -16.50],   // 拉巴斯(政府驻地;苏克雷是法定首都)
  EC: [-78.47, -0.18],    // 基多
  VE: [-66.90, 10.49],    // 加拉加斯
  PY: [-57.58, -25.26],   // 亚松森
  CU: [-82.36, 23.14],    // 哈瓦那
  DO: [-69.93, 18.48],    // 圣多明各
};

/** 取国家示意中心点(流向动画/散点定位用);未收录返回 undefined。 */
export function countryCenter(iso2: string): [number, number] | undefined {
  const upper = iso2.toUpperCase();
  const anchored = VISUAL_ANCHOR[upper];
  if (anchored) return anchored;
  registerWorldMap();
  const name = mapNameOf(upper);
  return name ? centroidByName.get(name) : undefined;
}
