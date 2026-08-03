import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { COUNTRIES } from "../mock/countries";
import { buildGlobeTexture } from "./globeTexture";

/** 经纬度 → 单位球面三维坐标(与 equirectangular 贴图对齐)。 */
function latLngToVec3(lat: number, lng: number, radius: number): THREE.Vector3 {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lng + 180) * (Math.PI / 180);
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

/** 大圆弧线(从起点到终点,带弧度)。 */
function arcPoints(
  start: THREE.Vector3,
  end: THREE.Vector3,
  segments = 64,
  altitude = 0.25,
): THREE.Vector3[] {
  const points: THREE.Vector3[] = [];
  const angle = start.angleTo(end);
  if (angle < 1e-6) return [start.clone(), end.clone()];
  const sin = Math.sin(angle);
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const a = Math.sin((1 - t) * angle) / sin;
    const b = Math.sin(t * angle) / sin;
    const p = new THREE.Vector3()
      .addScaledVector(start, a)
      .addScaledVector(end, b);
    const lift = 1 + altitude * Math.sin(Math.PI * t);
    p.normalize().multiplyScalar(start.length() * lift);
    points.push(p);
  }
  return points;
}

interface GlobeInnerProps {
  radius?: number;
  autoRotateSpeed?: number;
}

function GlobeInner({ radius = 1.6, autoRotateSpeed = 0.1 }: GlobeInnerProps) {
  const groupRef = useRef<THREE.Group>(null);

  useFrame((_, delta) => {
    if (groupRef.current) {
      groupRef.current.rotation.y += delta * autoRotateSpeed;
    }
  });

  // 国家贴图(亮色页面里用深色球形成对比焦点:海洋深蓝 + 陆地中蓝)
  const texture = useMemo(() => {
    const canvas = buildGlobeTexture({
      width: 2048,
      height: 1024,
      oceanColor: "#0d1e3f",
      landColor: "#2a4a8a",
      borderColor: "rgba(140, 180, 255, 0.55)",
      highlight: {
        CN: "#c8102e", // 中国(公安红)
        US: "#4f7fff", // 美(亮蓝,在深色球上对比度高)
      },
    });
    const tex = new THREE.CanvasTexture(canvas);
    tex.anisotropy = 4;
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }, []);

  const sphereGeo = useMemo(
    () => new THREE.SphereGeometry(radius, 96, 96),
    [radius],
  );

  // 108 国光点(在球面上方少许)
  const dots = useMemo(() => {
    const positions = new Float32Array(COUNTRIES.length * 3);
    COUNTRIES.forEach((c, i) => {
      const p = latLngToVec3(c.lat, c.lng, radius * 1.012);
      positions[i * 3] = p.x;
      positions[i * 3 + 1] = p.y;
      positions[i * 3 + 2] = p.z;
    });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geo;
  }, [radius]);

  // 传播弧:多国互发(Origin → Targets),颜色按源头分(深色球上的亮色弧)
  const { arcLines, arcHeads } = useMemo(() => {
    // (origin, targets, color)
    const flows: Array<[string, string[], string]> = [
      ["US", ["JP", "KR", "GB", "DE", "AU"], "#ff3b5c"],  // 美 → 亚/欧/澳(红)
      ["CN", ["RU", "KZ", "PK", "TH", "ZA"], "#4f7fff"],  // 中 → 周边/金砖(蓝)
      ["RU", ["BY", "KZ", "TR", "IN"], "#f59e0b"],        // 俄 → 独联体/土印(橙)
      ["GB", ["AU", "IN", "ZA", "CA"], "#a78bfa"],        // 英 → 英联邦(浅紫)
      ["DE", ["FR", "PL", "IT", "ES"], "#34d399"],        // 德 → 欧盟(浅绿)
      ["BR", ["AR", "MX", "PE"], "#22d3ee"],              // 巴西 → 拉美(青)
      ["IN", ["BD", "LK", "NP", "MM"], "#f472b6"],        // 印 → 南亚(粉)
    ];
    const lines: THREE.Line[] = [];
    const heads: THREE.Vector3[] = [];
    flows.forEach(([originCode, targetCodes, color]) => {
      const origin = COUNTRIES.find((c) => c.code === originCode);
      if (!origin) return;
      const start = latLngToVec3(origin.lat, origin.lng, radius);
      targetCodes.forEach((tCode) => {
        const t = COUNTRIES.find((c) => c.code === tCode);
        if (!t) return;
        const end = latLngToVec3(t.lat, t.lng, radius);
        const pts = arcPoints(start, end, 48, 0.22);
        const geo = new THREE.BufferGeometry().setFromPoints(pts);
        const mat = new THREE.LineBasicMaterial({
          color,
          transparent: true,
          opacity: 0.55,
        });
        lines.push(new THREE.Line(geo, mat));
        heads.push(end);
      });
    });
    return { arcLines: lines, arcHeads: heads };
  }, [radius]);

  // 弧末端光点几何
  const headGeo = useMemo(() => {
    const positions = new Float32Array(arcHeads.length * 3);
    arcHeads.forEach((p, i) => {
      positions[i * 3] = p.x;
      positions[i * 3 + 1] = p.y;
      positions[i * 3 + 2] = p.z;
    });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geo;
  }, [arcHeads]);

  return (
    <group ref={groupRef} rotation={[0.32, 0, 0]} position={[1.1, 0, 0]}>
      {/* 主球:国家贴图 */}
      <mesh geometry={sphereGeo}>
        <meshStandardMaterial
          map={texture}
          roughness={1}
          metalness={0}
          emissive="#1a2d5a"
          emissiveIntensity={0.2}
        />
      </mesh>

      {/* 国家光点 */}
      <points geometry={dots}>
        <pointsMaterial
          size={0.022}
          color="#6ba4ff"
          sizeAttenuation
          transparent
          opacity={0.9}
        />
      </points>

      {/* 传播弧 */}
      {arcLines.map((line, i) => (
        <primitive key={i} object={line} />
      ))}

      {/* 弧末端脉冲点 */}
      <points geometry={headGeo}>
        <pointsMaterial
          size={0.05}
          color="#ff3b5c"
          sizeAttenuation
          transparent
          opacity={1}
        />
      </points>

      {/* 外层光晕 */}
      <mesh>
        <sphereGeometry args={[radius * 1.18, 48, 48]} />
        <meshBasicMaterial
          color="#4f7fff"
          transparent
          opacity={0.06}
          side={THREE.BackSide}
        />
      </mesh>
    </group>
  );
}

interface GlobeProps {
  className?: string;
}

/** 3D 地球(全屏背景,懒加载使用)。 */
export default function Globe({ className = "" }: GlobeProps) {
  return (
    <div className={`lp-globe ${className}`} aria-hidden="true">
      <Canvas
        camera={{ position: [0, 0.6, 4.5], fov: 40 }}
        dpr={[1, 1.8]}
        gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
      >
        <ambientLight intensity={0.7} />
        <directionalLight position={[5, 3, 5]} intensity={0.8} color="#cfe1ff" />
        <pointLight position={[-6, -2, -4]} intensity={0.3} color="#6b7fff" />
        <GlobeInner />
      </Canvas>
    </div>
  );
}
