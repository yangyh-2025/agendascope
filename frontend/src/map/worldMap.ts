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

/** 后端 30 目标国（含地图接口可能出现的额外采集国）→ world-atlas 要素英文名。 */
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

/** 展开 Polygon/MultiPolygon 坐标，求外接框中心（示意用，非精确质心）。 */
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
 * 注册世界地图（幂等）。返回注册用的 GeoJSON，供测试与调试断言。
 * 后续 echarts 实例只需使用 map: 'world' 即可渲染，无需再传 geoJSON。
 */
export function registerWorldMap(): WorldGeoJson {
  if (registered) return registered;
  const topo = countries50m as unknown as Topology;
  const geo = feature(
    topo,
    topo.objects.countries,
  ) as unknown as WorldGeoJson;
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
