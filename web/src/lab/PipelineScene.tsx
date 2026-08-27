/**
 * NetPath 3D scene — procedural pipeline, instanced packets, offload arcs.
 * All geometry built in code (spec §2). Zero per-frame allocations (§11).
 */
import { useMemo, useRef } from 'react'
import * as THREE from 'three'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Html } from '@react-three/drei'
import { STATION_X, KIND_COLOR, MAX_PACKETS, STATION_NAMES, type NetPathEngine } from '../sim/sceneTypes'
import { HeaderLayers, FlowPaths, QueueViz, SectionClipping } from './TrafficViz'

// ── Shared materials ─────────────────────────────────────────────────────
const MAT = {
  housing: () =>
    new THREE.MeshStandardMaterial({ color: '#1a2028', roughness: 0.6, metalness: 0.55 }),
  frame: new THREE.MeshStandardMaterial({ color: '#8e979e', roughness: 0.35, metalness: 0.8 }),
  brass: new THREE.MeshStandardMaterial({ color: '#b08d57', roughness: 0.3, metalness: 0.9 }),
}

function Station({ index }: { index: number }) {
  const x = STATION_X[index]
  const selected = useSelectedStation() === index
  const opacity = usePipelineOpacity()
  const exploded = useExploded()
  const y = Math.sin(index * 1.7) * exploded * 2.2
  const glowRef = useRef<THREE.Mesh>(null!)

  const mat = useMemo(() => {
    const m = MAT.housing()
    m.transparent = true
    m.opacity = opacity
    return m
  }, [opacity])

  // Active-station emissive pulse — draws the eye to the focused stage.
  const prefersReducedMotion = useMemo(() => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches, [])
  useFrame(({ clock }) => {
    if (!selected || !glowRef.current || prefersReducedMotion) return
    const p = 0.55 + Math.sin(clock.elapsedTime * 3) * 0.25
    const gm = glowRef.current.material as THREE.MeshStandardMaterial
    gm.emissiveIntensity = p
    glowRef.current.scale.setScalar(1 + Math.sin(clock.elapsedTime * 3) * 0.06)
  })

  return (
    <group position={[x, y, 0]}>
      <mesh material={mat}>
        <boxGeometry args={[5.4, 4.4, 4.4]} />
      </mesh>
      <mesh>
        <icosahedronGeometry args={[1.15 + (index % 3) * 0.12, 1]} />
        <meshBasicMaterial
          color={selected ? '#f2c45f' : '#6fc7e8'}
          transparent
          opacity={selected ? 0.95 : 0.45}
        />
      </mesh>
      <mesh position={[0, -2.5, 0]} material={MAT.brass}>
        <boxGeometry args={[5.8, 0.28, 4.8]} />
      </mesh>
      <mesh position={[0, 2.5, 0]} material={MAT.frame}>
        <boxGeometry args={[5.8, 0.22, 4.8]} />
      </mesh>
      {selected && (
        <mesh ref={glowRef} rotation={[Math.PI / 2, 0, 0]} position={[0, -3.35, 0]}>
          <torusGeometry args={[1.15, 0.05, 10, 40]} />
          <meshStandardMaterial
            color="#f2c45f"
            emissive="#f2c45f"
            emissiveIntensity={0.6}
            toneMapped={false}
          />
        </mesh>
      )}
      <Html
        position={[0, 3.4, 0]}
        center
        distanceFactor={28}
        occlude={false}
        style={{ pointerEvents: 'none' }}
      >
        <div className={`select-none whitespace-nowrap rounded border px-2 py-0.5 font-mono text-[10px] tracking-wide backdrop-blur ${
          selected
            ? 'border-[#f2c45f]/60 bg-[rgba(242,196,95,0.12)] text-[#f2c45f]'
            : 'border-[#30363d] bg-[rgba(7,9,12,0.7)] text-[#9d978a]'
        }`}>
          {STATION_NAMES[index]}
        </div>
      </Html>
    </group>
  )
}

// Small selector hooks to keep component bodies clean.
import { useLab } from '../lab/store'
function useSelectedStation() {
  return useLab((s) => s.selectedStation)
}
function usePipelineOpacity() {
  return useLab((s) => s.pipelineOpacity)
}
function useExploded() {
  return useLab((s) => s.exploded)
}

function Conduits() {
  const opacity = usePipelineOpacity()
  const exploded = useExploded()

  const items = useMemo(
    () =>
      STATION_X.slice(0, -1).map((x0: number, i: number) => {
        const x1 = STATION_X[i + 1]
        return {
          x: (x0 + x1) / 2,
          len: x1 - x0 - 5.4,
          y: ((Math.sin(i * 1.7) + Math.sin((i + 1) * 1.7)) / 2) * exploded * 2.2,
        }
      }),
    [exploded],
  )

  const mat = useMemo(
    () =>
      new THREE.MeshPhysicalMaterial({
        color: '#0d1420',
        transparent: true,
        opacity: Math.min(opacity, 0.65),
        roughness: 0.2,
        metalness: 0.3,
      }),
    [opacity],
  )

  return (
    <>
      {items.map(({ x, len, y }: { x: number; len: number; y: number }, i: number) => (
        <group key={i} position={[x, y, 0]} rotation={[0, 0, Math.PI / 2]}>
          <mesh material={mat}>
            <cylinderGeometry args={[0.85, 0.85, len, 16, 1, true]} />
          </mesh>
          <mesh position={[len / 2 - 0.25, 0, 0]} material={MAT.brass}>
            <cylinderGeometry args={[1.0, 1.0, 0.5, 16]} />
          </mesh>
          <mesh position={[-len / 2 + 0.25, 0, 0]} material={MAT.brass}>
            <cylinderGeometry args={[1.0, 1.0, 0.5, 16]} />
          </mesh>
        </group>
      ))}
    </>
  )
}

function FastPathArc() {
  const enabled = useLab((s) => s.layers.offloadDecisions)
  const geom = useMemo(() => {
    const curve = new THREE.QuadraticBezierCurve3(
      new THREE.Vector3(STATION_X[4], 0, 0),
      new THREE.Vector3((STATION_X[4] + STATION_X[7]) / 2, 14, -6),
      new THREE.Vector3(STATION_X[7], 0, 0),
    )
    return new THREE.TubeGeometry(curve, 48, 0.32, 10, false)
  }, [])

  if (!enabled) return null
  return (
    <mesh geometry={geom}>
      <meshBasicMaterial color="#39d353" transparent opacity={0.4} />
    </mesh>
  )
}

function Packets({ engine }: { engine: NetPathEngine }) {
  const meshRef = useRef<THREE.InstancedMesh>(null!)
  const dummy = useMemo(() => new THREE.Object3D(), [])
  const color = useMemo(() => new THREE.Color(), [])
  const tmpColor = useMemo(() => new THREE.Color('#ffffff'), [])

  useFrame(() => {
    const mesh = meshRef.current
    if (!mesh) return
    let drawn = 0
    for (let i = 0; i < MAX_PACKETS; i++) {
      const p = engine.packets[i]
      if (!p.active || p.dropped) continue

      let y = 0
      let z = 0
      if (p.fastPath && p.progress >= 4.02 && p.progress < 7) {
        const t = Math.min(1, (p.progress - 4) / 3)
        y = Math.sin(t * Math.PI) * 13
        z = -Math.sin(t * Math.PI) * 5.5
      }
      dummy.position.set(
        STATION_X[0] + p.progress * ((STATION_X[7] - STATION_X[0]) / 7), y, z)
      dummy.scale.setScalar((p.kind === 'bulk' ? 1.15 : p.kind === 'video' ? 0.85 : p.kind === 'voice' ? 0.7 : 0.55) * (p.traced ? 1.6 : 1))
      dummy.updateMatrix()
      mesh.setMatrixAt(drawn, dummy.matrix)

      color.set(KIND_COLOR[p.kind])
      if (p.traced) color.lerp(tmpColor, 0.5)
      mesh.setColorAt(drawn, color)
      drawn++
    }
    mesh.count = drawn
    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
  })

  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, MAX_PACKETS]} frustumCulled={false}>
      <sphereGeometry args={[0.42, 12, 10]} />
      <meshBasicMaterial toneMapped={false} />
    </instancedMesh>
  )
}

const CAMERA_PRESETS: Record<string, [number, number, number]> = {
  overview: [0, 22, 62],
  section: [24, 12, 40],
  parser: [STATION_X[1], 8, 24],
  classifier: [STATION_X[3], 8, 24],
  offload: [STATION_X[4], 10, 24],
  free: [0, 22, 62],
}

function CameraRig() {
  const preset = useLab((s) => s.cameraPreset)
  const target = useMemo(() => new THREE.Vector3(0, 0, 0), [])
  const { camera } = useThree()
  const goal = useMemo(() => new THREE.Vector3(...CAMERA_PRESETS[preset]), [preset])
  // Suspend lerping while the user drags — otherwise OrbitControls input
  // decays back to the preset within ~1s, making Free Inspection unusable.
  const userInteracting = useRef(false)

  useFrame(() => {
    if (userInteracting.current || preset === 'free') return
    camera.position.lerp(goal, 0.06)
    camera.lookAt(target)
  })

  return (
    <OrbitControls
      target={target}
      enablePan={false}
      enableDamping
      dampingFactor={0.08}
      minDistance={14}
      maxDistance={110}
      onStart={() => { userInteracting.current = true }}
      onEnd={() => { userInteracting.current = false }}
    />
  )
}

export function PipelineScene({ engine, isMobile }: { engine: NetPathEngine; isMobile: boolean }) {
  // spec §7: pixel ratio ≤1.5 mobile, ≤2 desktop
  const dprMax = isMobile ? 1.5 : 2
  const camInit = isMobile
    ? [0, 30, 70] as const   // raised + pulled back so full pipeline fits portrait
    : CAMERA_PRESETS.overview

  return (
    <Canvas
      dpr={[1, dprMax]}
      camera={{ position: [...camInit], fov: isMobile ? 50 : 42, near: 0.5, far: 300 }}
      gl={{
        antialias: true,
        powerPreference: 'high-performance',
        localClippingEnabled: true,
      }}
      onCreated={({ gl, scene }) => {
        scene.background = new THREE.Color('#05070a')
        gl.setClearColor('#05070a')
      }}
    >
      <ambientLight intensity={0.45} />
      <directionalLight position={[18, 30, 20]} intensity={1.3} color="#cfe8ff" />
      <directionalLight position={[-20, 12, -14]} intensity={0.5} color="#b08d57" />
      <spotLight position={[0, 40, 10]} angle={0.6} penumbra={0.8} intensity={0.6} color="#6fc7e8" />
      <pointLight position={[STATION_X[4], 8, 6]} intensity={30} distance={30} color="#39d353" />

      {/* Reflective dark floor — adds depth so the pipeline reads as a stage */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -4.4, 0]} receiveShadow>
        <planeGeometry args={[200, 90]} />
        <meshStandardMaterial color="#0a0e14" roughness={0.35} metalness={0.65} />
      </mesh>

      <gridHelper args={[140, 46, '#1c2530', '#12181f']} position={[0, -4.2, 0]} />

      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
        <Station key={i} index={i} />
      ))}
      <Conduits />
      <FastPathArc />
      <Packets engine={engine} />
      <HeaderLayers />
      <FlowPaths />
      <QueueViz engine={engine} />
      <SectionClipping />

      <CameraRig />
    </Canvas>
  )
}
