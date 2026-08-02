/**
 * 离线世界地图（T4.6 前置修复）：
 * - 构建期把 world-atlas 的 TopoJSON（countries-50m；110m 缺少新加坡等小国土要素）转成 GeoJSON 并 echarts.registerMap('world')
 * - GeoJSON 随前端包离线打包，运行时绝不从公网拉取地图数据
 * - 提供 ISO-3166 alpha2 → 地图要素英文名 映射与各国外接框中心（流向动画取点用）
 */
import * as echarts from "echarts";
import { feature } from "topojson-client";
import type { Topology } from "topojson-specification";
import countries50m from "world-atlas/countries-50m.json";

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

/** 展开 Polygon/MultiPolygon 坐标,求外接框中心(示意用,非精确质心)。 */
function bboxCenter(coords: unknown): [number, number] | null {
  let minLng = Infinity;
  let maxLng = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  const walk = (node: unknown): void => {
    if (!Array.isArray(node)) return;
    if (typeof node[0] === "number" && typeof node[1] === "number") {
      const [lng, lat] = node as [number, number];
      if (lng < minLng) minLng = lng;
      if (lng > maxLng) maxLng = lng;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
      return;
    }
    for (const child of node) walk(child);
  };
  walk(coords);
  if (!Number.isFinite(minLng)) return null;
  return [(minLng + maxLng) / 2, (minLat + maxLat) / 2];
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
export function registerWorldMap(): WorldGeoJson {
  if (registered) return registered;
  const topo = countries50m as unknown as Topology;
  const geo = feature(
    topo,
    topo.objects.countries,
  ) as unknown as WorldGeoJson;
  clipAntimeridianFragments(geo);
  for (const f of geo.features) {
    const name = f.properties?.name;
    if (!name) continue;
    const center = bboxCenter(f.geometry?.coordinates);
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

/** 取国家示意中心点（流向动画/散点定位用）；未收录返回 undefined。 */
export function countryCenter(iso2: string): [number, number] | undefined {
  registerWorldMap();
  const name = mapNameOf(iso2);
  return name ? centroidByName.get(name) : undefined;
}
