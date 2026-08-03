import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { COUNTRIES } from "../mock/countries";

/** 经纬度 → 单位球面三维坐标。 */
function latLngToVec3(lat: number, lng: number, radius: number): THREE.Vector3 {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lng + 180) * (Math.PI / 180);
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

/** 大圆插值,生成弧线点。 */
function arcPoints(
  start: THREE.Vector3,
  end: THREE.Vector3,
  segments = 48,
  altitude = 0.18,
): THREE.Vector3[] {
  const points: THREE.Vector3[] = [];
  const angle = start.angleTo(end);
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const sin = Math.sin(angle);
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

function GlobeInner({ radius = 1.6, autoRotateSpeed = 0.15 }: GlobeInnerProps) {
  const groupRef = useRef<THREE.Group>(null);

  useFrame((_, delta) => {
    if (groupRef.current) {
      groupRef.current.rotation.y += delta * autoRotateSpeed;
    }
  });

  // 基础球体(深色)+ 网格
  const sphereGeo = useMemo(
    () => new THREE.SphereGeometry(radius * 0.985, 64, 64),
    [radius],
  );
  const wireGeo = useMemo(
    () => new THREE.SphereGeometry(radius, 24, 24),
    [radius],
  );

  // 108 国光点
  const dotsGeo = useMemo(() => {
    const positions = new Float32Array(COUNTRIES.length * 3);
    COUNTRIES.forEach((c, i) => {
      const p = latLngToVec3(c.lat, c.lng, radius * 1.005);
      positions[i * 3] = p.x;
      positions[i * 3 + 1] = p.y;
      positions[i * 3 + 2] = p.z;
    });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geo;
  }, [radius]);

  // 演示传播弧(从美国到亚/欧/拉)
  const arcs = useMemo(() => {
    const origin = COUNTRIES.find((c) => c.code === "US");
    if (!origin) return [];
    const targets = ["JP", "KR", "IN", "GB", "DE", "BR", "AU", "ZA"]
      .map((code) => COUNTRIES.find((c) => c.code === code))
      .filter((c): c is NonNullable<typeof c> => Boolean(c));
    const start = latLngToVec3(origin.lat, origin.lng, radius);
    return targets.map((t) => {
      const end = latLngToVec3(t.lat, t.lng, radius);
      const pts = arcPoints(start, end, 48, 0.18);
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      return geo;
    });
  }, [radius]);

  return (
    <group ref={groupRef} rotation={[0.35, 0, 0]}>
      {/* 主球体(深色) */}
      <mesh geometry={sphereGeo}>
        <meshBasicMaterial color="#0b1a3a" transparent opacity={0.85} />
      </mesh>
      {/* 网格线 */}
      <mesh geometry={wireGeo}>
        <meshBasicMaterial
          color="#2a4a8a"
          wireframe
          transparent
          opacity={0.18}
        />
      </mesh>
      {/* 国家光点 */}
      <points geometry={dotsGeo}>
        <pointsMaterial
          size={0.025}
          color="#4f7fff"
          sizeAttenuation
          transparent
          opacity={0.95}
        />
      </points>
      {/* 传播弧 */}
      {arcs.map((geo, i) => (
        <primitive key={i} object={new THREE.Line(geo, arcMaterial())} />
      ))}
      {/* 光晕 */}
      <mesh>
        <sphereGeometry args={[radius * 1.12, 32, 32]} />
        <meshBasicMaterial
          color="#4f7fff"
          transparent
          opacity={0.04}
          side={THREE.BackSide}
        />
      </mesh>
    </group>
  );
}

function arcMaterial(): THREE.LineBasicMaterial {
  return new THREE.LineBasicMaterial({
    color: "#ff3b5c",
    transparent: true,
    opacity: 0.7,
  });
}

interface GlobeProps {
  className?: string;
}

/** 3D 地球(懒加载使用,主入口动态 import)。 */
export default function Globe({ className = "" }: GlobeProps) {
  return (
    <div className={`lp-globe ${className}`} aria-hidden="true">
      <Canvas
        camera={{ position: [0, 0, 4.2], fov: 42 }}
        dpr={[1, 1.6]}
        gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
        frameloop="always"
      >
        <ambientLight intensity={0.7} />
        <pointLight position={[6, 4, 6]} intensity={0.7} color="#6ba4ff" />
        <GlobeInner />
      </Canvas>
    </div>
  );
}
