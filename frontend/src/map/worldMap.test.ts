import { describe, expect, it } from "vitest";
import {
  countryCenter,
  ISO2_TO_MAP_NAME,
  mapNameOf,
  registerWorldMap,
  WORLD_MAP_NAME,
} from "./worldMap";

describe("离线世界地图注册", () => {
  it("注册的 GeoJSON 要素数 >100（覆盖全球主要国家）", () => {
    const geo = registerWorldMap();
    expect(geo.type).toBe("FeatureCollection");
    expect(geo.features.length).toBeGreaterThan(100);
  });

  it("注册幂等：重复调用返回同一份 GeoJSON", () => {
    const a = registerWorldMap();
    const b = registerWorldMap();
    expect(a).toBe(b);
  });

  it("30 个目标国全部能映射到地图上真实存在的要素名", () => {
    const geo = registerWorldMap();
    const names = new Set(geo.features.map((f) => f.properties?.name));
    const missing: string[] = [];
    for (const [code, mapName] of Object.entries(ISO2_TO_MAP_NAME)) {
      if (!names.has(mapName)) missing.push(`${code}:${mapName}`);
    }
    expect(missing).toEqual([]);
  });

  it("mapNameOf / countryCenter 行为正确", () => {
    expect(mapNameOf("cn")).toBe("China");
    expect(mapNameOf("US")).toBe("United States of America");
    expect(mapNameOf("XX")).toBeUndefined();
    const cn = countryCenter("CN");
    expect(cn).toBeDefined();
    // 中国经度大致在 73E-135E、纬度 18N-53N
    expect(cn![0]).toBeGreaterThan(60);
    expect(cn![0]).toBeLessThan(150);
    expect(cn![1]).toBeGreaterThan(10);
    expect(cn![1]).toBeLessThan(60);
  });

  it("地图注册名为 world", () => {
    expect(WORLD_MAP_NAME).toBe("world");
  });
});
