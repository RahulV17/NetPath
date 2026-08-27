/**
 * TrafficViz — the three dormant visualization layers, now real:
 *
 *  headerLayers  — stacked translucent plates above packets near the parser
 *  flowPaths     — ribbon trails along the conduit for active flows
 *  queueDepth    — instanced cubes stacking up at the QoS shaper
 *
 * Plus SectionClip: a clipping plane that cuts the pipeline at Station 6
 * when section view is enabled.
 */
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import { useFrame, useThree } from '@react-three/fiber'
import { useLab } from './store'
import { STATION_X } from '../sim/sceneTypes'
import type { NetPathEngine } from '../sim/engine'

// ── Header layers: plates stacked over the packet stream at Station 1 ──

const LAYER_LABELS = ['Eth', 'IP', 'TCP/UDP', 'Payload']

export function HeaderLayers() {
  const enabled = useLab((s) => s.layers.headerLayers)
  const group = useRef<THREE.Group>(null!)
  const t = useRef(0)

  const plateGeo = useMemo(() => new THREE.BoxGeometry(2.2, 0.16, 1.4), [])
  const mats = useMemo(
    () =>
      ['#8e979e', '#6fc7e8', '#b08d57', '#e8e0cc'].map(
        (c) =>
          new THREE.MeshBasicMaterial({
            color: c,
            transparent: true,
            opacity: 0.55,
          }),
      ),
    [],
  )

  useFrame((_, dt) => {
    t.current += dt
    const g = group.current
    if (!g) return
    // gentle bob so layers read as "being decoded"
    g.children.forEach((child, i) => {
      child.position.y = 3.4 + i * 0.42 + Math.sin(t.current * 2 + i) * 0.08
      child.rotation.y = Math.sin(t.current * 0.7 + i) * 0.15
    })
  })

  if (!enabled) return null
  return (
    <group ref={group} position={[STATION_X[1], 0, 0]}>
      {LAYER_LABELS.map((label, i) => (
        <mesh key={label} geometry={plateGeo} material={mats[i]} />
      ))}
    </group>
  )
}

// ── Flow paths: moving dash ribbons inside conduits ─────────────────────

export function FlowPaths() {
  const enabled = useLab((s) => s.layers.flowPaths)

  const geoms = useMemo(() => {
    return STATION_X.slice(0, -1).map((x0: number, i: number) => {
      const x1 = STATION_X[i + 1]
      return new THREE.CylinderGeometry(0.18, 0.18, x1 - x0 - 5.4, 8, 1, true)
    })
  }, [])

  // ONE shared material — every conduit animates together (spec §11: reuse)
  const mat = useMemo(() => {
    const c = document.createElement('canvas')
    c.width = 64
    c.height = 4
    const ctx = c.getContext('2d')!
    ctx.clearRect(0, 0, 64, 4)
    ctx.fillStyle = '#6fc7e8'
    for (let x = 0; x < 64; x += 16) ctx.fillRect(x, 0, 8, 4)
    const tex = new THREE.CanvasTexture(c)
    tex.wrapS = THREE.RepeatWrapping
    tex.repeat.set(6, 1)
    const m = new THREE.MeshBasicMaterial({
      color: '#6fc7e8',
      transparent: true,
      opacity: 0.35,
      map: tex,
    })
    return m
  }, [])

  useFrame((_, dt) => {
    if (!enabled || !mat.map) return
    mat.map.offset.x -= dt * 0.8
  })

  if (!enabled) return null
  return (
    <group>
      {geoms.map((g, i) => {
        const midX = (STATION_X[i] + STATION_X[i + 1]) / 2
        return (
          <mesh key={i} geometry={g} material={mat} position={[midX, 0, 0]} rotation={[0, 0, Math.PI / 2]} />
        )
      })}
    </group>
  )
}

// ── Queue depth: instanced cubes stacking at the QoS shaper ─────────────

const QUEUE_MAX = 40

export function QueueViz({ engine }: { engine: NetPathEngine }) {
  const enabled = useLab((s) => s.layers.queueDepth)
  const meshRef = useRef<THREE.InstancedMesh>(null!)
  const dummy = useMemo(() => new THREE.Object3D(), [])

  useFrame(() => {
    const mesh = meshRef.current
    if (!mesh) return
    const depth = Math.min(engine.stats.queueDepth, QUEUE_MAX)

    let n = 0
    for (let row = 0; row < 5 && n < depth; row++) {
      for (let col = 0; col < 8 && n < depth; col++) {
        dummy.position.set(
          STATION_X[6] + col * 0.55 - 2,
          2.6 + row * 0.5,
          0,
        )
        dummy.scale.setScalar(0.22)
        dummy.updateMatrix()
        mesh.setMatrixAt(n, dummy.matrix)
        n++
      }
    }
    mesh.count = n
    mesh.instanceMatrix.needsUpdate = true
  })

  if (!enabled) return null
  return (
    <instancedMesh ref={meshRef} args={[undefined, undefined, QUEUE_MAX]} frustumCulled={false}>
      <boxGeometry args={[1, 1, 1]} />
      <meshBasicMaterial color="#f08a4b" transparent opacity={0.85} toneMapped={false} />
    </instancedMesh>
  )
}

// ── Section view clipping plane ─────────────────────────────────────────
// Cuts the pipeline horizontally so learners see the interior. Animated:
// eases between below-floor (off) and mid-housing (on). Note the fast-path
// arc apex (y=14) and header plates (y≈3.4) sit ABOVE the cut at 1.2 and
// stay visible — intentional, they're context, not cutaway targets.

export function SectionClipping() {
  const sectionView = useLab((s) => s.sectionView)
  const plane = useMemo(() => new THREE.Plane(new THREE.Vector3(0, -1, 0), -10), [])
  const { gl } = useThree()

  useEffect(() => {
    gl.localClippingEnabled = true
    return () => {
      gl.clippingPlanes = []
    }
  }, [gl])

  useFrame(() => {
    const goal = sectionView ? 1.2 : -10
    plane.constant += (goal - plane.constant) * 0.12
    gl.clippingPlanes = sectionView || plane.constant > -9 ? [plane] : []
  })

  return null
}
