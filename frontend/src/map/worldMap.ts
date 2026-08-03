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

/** 取国家示意中心点（流向动画/散点定位用）；未收录返回 undefined。 */
export function countryCenter(iso2: string): [number, number] | undefined {
  registerWorldMap();
  const name = mapNameOf(iso2);
  return name ? centroidByName.get(name) : undefined;
}
