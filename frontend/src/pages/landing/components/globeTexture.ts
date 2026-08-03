/**
 * 用 world-atlas TopoJSON 在 Canvas 上绘制国家边界,作为 3D 球面贴图。
 * equirectangular 投影:lng ∈ [-180, 180] → x ∈ [0, w],lat ∈ [90, -90] → y ∈ [0, h]
 *
 * 合规修补(chinaCompliance.ts):
 * - 藏南、阿克赛钦以补丁 Polygon 叠加绘制,颜色与中国一致
 * - 台湾在 world-atlas 中作为独立要素,此处按与中国同色绘制(一个中国原则)
 */
import { feature } from "topojson-client";
import type { Topology, GeometryCollection } from "topojson-specification";
import type { FeatureCollection, Geometry, Position } from "geojson";
import countries50m from "world-atlas/countries-50m.json";
import { CHINA_COMPLIANCE_PATCHES, TAIWAN_POLYGON } from "../../../map/chinaCompliance";

export interface GlobeTextureOptions {
  width?: number;
  height?: number;
  /** 海洋底色 */
  oceanColor?: string;
  /** 国家填充 */
  landColor?: string;
  /** 国家边界 */
  borderColor?: string;
  /** 高亮国(ISO alpha-2 → 高亮颜色) */
  highlight?: Record<string, string>;
  /** world-atlas 要素 id (numeric ISO) → ISO alpha-2 映射 */
  idToIso2?: Record<string, string>;
}

/** world-atlas 的 feature.id 是 numeric ISO-3166(三位数),映射到 alpha-2。 */
const NUM_TO_ALPHA2: Record<string, string> = {
  "004": "AF", "008": "AL", "012": "DZ", "024": "AO", "032": "AR", "036": "AU",
  "040": "AT", "031": "AZ", "044": "BS", "048": "BH", "050": "BD", "112": "BY",
  "056": "BE", "084": "BZ", "204": "BJ", "064": "BT", "068": "BO", "070": "BA",
  "072": "BW", "076": "BR", "096": "BN", "100": "BG", "854": "BF", "108": "BI",
  "116": "KH", "120": "CM", "124": "CA", "140": "CF", "148": "TD", "152": "CL",
  "156": "CN", "170": "CO", "178": "CG", "180": "CD", "188": "CR", "384": "CI",
  "191": "HR", "192": "CU", "196": "CY", "203": "CZ", "208": "DK", "262": "DJ",
  "214": "DO", "218": "EC", "818": "EG", "222": "SV", "226": "GQ", "232": "ER",
  "233": "EE", "231": "ET", "242": "FJ", "246": "FI", "250": "FR", "266": "GA",
  "270": "GM", "268": "GE", "276": "DE", "288": "GH", "300": "GR", "320": "GT",
  "324": "GN", "624": "GW", "328": "GY", "332": "HT", "340": "HN", "348": "HU",
  "352": "IS", "356": "IN", "360": "ID", "364": "IR", "368": "IQ", "372": "IE",
  "376": "IL", "380": "IT", "388": "JM", "392": "JP", "400": "JO", "398": "KZ",
  "404": "KE", "408": "KP", "410": "KR", "414": "KW", "417": "KG", "418": "LA",
  "428": "LV", "422": "LB", "426": "LS", "430": "LR", "434": "LY", "440": "LT",
  "442": "LU", "807": "MK", "450": "MG", "454": "MW", "458": "MY", "466": "ML",
  "478": "MR", "484": "MX", "498": "MD", "496": "MN", "499": "ME", "504": "MA",
  "508": "MZ", "104": "MM", "516": "NA", "524": "NP", "528": "NL", "554": "NZ",
  "558": "NI", "562": "NE", "566": "NG", "578": "NO", "512": "OM", "586": "PK",
  "591": "PA", "598": "PG", "600": "PY", "604": "PE", "608": "PH", "616": "PL",
  "620": "PT", "634": "QA", "642": "RO", "643": "RU", "646": "RW", "682": "SA",
  "686": "SN", "688": "RS", "694": "SL", "702": "SG", "703": "SK", "705": "SI",
  "090": "SB", "706": "SO", "710": "ZA", "724": "ES", "144": "LK", "729": "SD",
  "740": "SR", "748": "SZ", "752": "SE", "756": "CH", "760": "SY", "158": "TW",
  "762": "TJ", "834": "TZ", "764": "TH", "626": "TL", "768": "TG", "780": "TT",
  "788": "TN", "792": "TR", "795": "TM", "800": "UG", "804": "UA", "784": "AE",
  "826": "GB", "840": "US", "858": "UY", "860": "UZ", "862": "VE", "704": "VN",
  "887": "YE", "894": "ZM", "716": "ZW", "275": "PS",
};

function project(pos: Position, w: number, h: number): [number, number] {
  const [lng, lat] = pos;
  return [((lng + 180) / 360) * w, ((90 - lat) / 180) * h];
}

function drawPolygon(
  ctx: CanvasRenderingContext2D,
  coords: Position[][],
  w: number,
  h: number,
) {
  coords.forEach((ring) => {
    ctx.beginPath();
    ring.forEach((p, i) => {
      const [x, y] = project(p, w, h);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
  });
}

/** 生成 equirectangular 国家贴图,供 three.js 球面使用。 */
export function buildGlobeTexture({
  width = 2048,
  height = 1024,
  oceanColor = "#081228",
  landColor = "#1a2d5a",
  borderColor = "rgba(120,160,255,0.35)",
  highlight = {},
}: GlobeTextureOptions = {}): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;

  // 海洋底
  ctx.fillStyle = oceanColor;
  ctx.fillRect(0, 0, width, height);

  // 国家
  const topo = countries50m as unknown as Topology<{
    countries: GeometryCollection;
  }>;
  const geo = feature(
    topo,
    topo.objects.countries,
  ) as unknown as FeatureCollection<Geometry>;

  const highlightIso = new Set(Object.keys(highlight));

  geo.features.forEach((f) => {
    const numId = (f.id as string) ?? "";
    const iso2 = NUM_TO_ALPHA2[numId];
    // 台湾(TW)在合规上等同中国(CN)着色:若 highlight 含 CN 不含 TW,自动按 CN 填色
    const effectiveIso = iso2 === "TW" && highlight["CN"] && !highlight["TW"] ? "CN" : iso2;
    const fill = effectiveIso && highlight[effectiveIso] ? highlight[effectiveIso] : landColor;

    ctx.fillStyle = fill;
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = 0.6;

    if (f.geometry.type === "Polygon") {
      drawPolygon(ctx, f.geometry.coordinates as Position[][], width, height);
      ctx.fill();
      ctx.stroke();
    } else if (f.geometry.type === "MultiPolygon") {
      (f.geometry.coordinates as Position[][][]).forEach((poly) => {
        drawPolygon(ctx, poly, width, height);
        ctx.fill();
        ctx.stroke();
      });
    }
    // 高亮国加光晕(降低强度,避免覆盖国界线)
    if (effectiveIso && highlightIso.has(effectiveIso)) {
      ctx.save();
      ctx.shadowColor = highlight[effectiveIso];
      ctx.shadowBlur = 4;
      if (f.geometry.type === "Polygon") {
        drawPolygon(ctx, f.geometry.coordinates as Position[][], width, height);
        ctx.fill();
      } else if (f.geometry.type === "MultiPolygon") {
        (f.geometry.coordinates as Position[][][]).forEach((poly) => {
          drawPolygon(ctx, poly, width, height);
          ctx.fill();
        });
      }
      ctx.restore();
    }
  });

  // 合规补丁:藏南、阿克赛钦按中国颜色叠加
  const chinaFill = highlight["CN"] ?? landColor;
  ctx.fillStyle = chinaFill;
  ctx.strokeStyle = borderColor;
  ctx.lineWidth = 0.6;
  CHINA_COMPLIANCE_PATCHES.forEach((patch) => {
    drawPolygon(ctx, [patch.polygon as Position[]], width, height);
    ctx.fill();
    ctx.stroke();
    // 中国高亮时补丁同步发光(低强度)
    if (highlight["CN"]) {
      ctx.save();
      ctx.shadowColor = highlight["CN"];
      ctx.shadowBlur = 4;
      drawPolygon(ctx, [patch.polygon as Position[]], width, height);
      ctx.fill();
      ctx.restore();
    }
  });

  // 台湾(world-atlas 数据中可能为独立要素,此补丁确保与中国同色)
  if (!CHINA_COMPLIANCE_PATCHES.some((p) => p.name === "Taiwan")) {
    ctx.fillStyle = chinaFill;
    drawPolygon(ctx, [TAIWAN_POLYGON as Position[]], width, height);
    ctx.fill();
    if (highlight["CN"]) {
      ctx.save();
      ctx.shadowColor = highlight["CN"];
      ctx.shadowBlur = 4;
      drawPolygon(ctx, [TAIWAN_POLYGON as Position[]], width, height);
      ctx.fill();
      ctx.restore();
    }
  }

  return canvas;
}
