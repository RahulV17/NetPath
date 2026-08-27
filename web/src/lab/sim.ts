/**
 * Simulation core — procedural packet flow through the 8-station pipeline.
 *
 * Design rules (spec §11): no allocations in the animation loop, instanced
 * rendering, geometry/material reuse. This module owns a fixed-size packet
 * pool mutated in place; React never sees per-frame updates.
 */
import { STATIONS, useLab, simMirror } from './store'

export const MAX_PACKETS = 220
const STATION_COUNT = STATIONS.length

/** Station X positions along the pipeline (world units). */
export const STATION_X = Array.from(
  { length: STATION_COUNT },
  (_, i) => -31 + i * (62 / (STATION_COUNT - 1)),
)

export type PacketVisualKind =
  | 'voice'    // small, warm
  | 'video'    // medium, cyan
  | 'bulk'     // large, nickel
  | 'control'  // tiny, bone

interface Packet {
  active: boolean
  x: number          // world X along the pipeline
  speed: number      // units/sec
  size: number       // visual radius scale
  kind: PacketVisualKind
  /** Which path this packet takes after the offload decision. */
  fastPath: boolean
  /** Progress 0..7 — current station index (fractional while traveling). */
  progress: number
  /** True when selected by "Follow One Packet". */
  traced: boolean
  /** Per-packet deterministic hash for readout display. */
  hash: string
}

function makePool(): Packet[] {
  return Array.from({ length: MAX_PACKETS }, () => ({
    active: false,
    x: 0,
    speed: 0,
    size: 1,
    kind: 'control' as PacketVisualKind,
    fastPath: true,
    progress: 0,
    traced: false,
    hash: '',
  }))
}

const KIND_COLOR: Record<PacketVisualKind, string> = {
  voice: '#f2c45f',   // flame
  video: '#6fc7e8',   // air
  bulk: '#b08d57',    // nickel
  control: '#e8e0cc', // bone
}

export { KIND_COLOR }

function randomKind(mode: 'mixed' | 'voiceHeavy' | 'bulkHeavy'): PacketVisualKind {
  const r = Math.random()
  if (mode === 'voiceHeavy') return r < 0.65 ? 'voice' : r < 0.85 ? 'video' : 'bulk'
  if (mode === 'bulkHeavy') return r < 0.6 ? 'bulk' : r < 0.85 ? 'video' : 'voice'
  return r < 0.4 ? 'video' : r < 0.7 ? 'voice' : r < 0.9 ? 'bulk' : 'control'
}

const KIND_SIZE: Record<PacketVisualKind, number> = {
  voice: 0.55,
  video: 0.85,
  bulk: 1.15,
  control: 0.4,
}

function shortHash(): string {
  return Math.floor(Math.random() * 0xffff)
    .toString(16)
    .padStart(4, '0')
}

/**
 * Mutable simulation state. Created once; `step(dt)` mutates it.
 */
export class PipelineSim {
  packets: Packet[] = makePool()
  /** Spawn accumulator — fractional packets carried across frames. */
  private spawnDebt = 0

  /** Aggregate stats for readouts (throttled consumers). */
  stats = {
    spawnedTotal: 0,
    fastPathCount: 0,
    slowPathCount: 0,
    queueDepth: 0,
  }

  step(dt: number) {
    const lab = useLab.getState()
    const running = simMirror.running
    const rate = lab.packetRate / 100 // normalized 0..1
    const mode = lab.trafficMode

    // ── Handle state transitions driven by the loop ──
    if (lab.pathState === 'Starting') {
      // brief startup beat, then run
      useLab.setState({ pathState: rate > 0 ? 'Parsing' : 'Running' })
    } else if (lab.pathState === 'Stopping') {
      if (!this.packets.some((p) => p.active)) {
        useLab.setState({ pathState: 'Idle' })
      }
    }

    if (!running && !this.packets.some((p) => p.active)) {
      this.stats.queueDepth = 0
      return
    }

    // ── Spawn new packets at ingress ──
    if (running) {
      this.spawnDebt += dt * (2 + rate * 38) // up to ~40 pkt/s visual
      while (this.spawnDebt >= 1) {
        this.spawnDebt -= 1
        this.spawn(mode)
      }
    }

    // ── Move packets; apply stage effects as they cross stations ──
    let maxQueue = 0
    for (const p of this.packets) {
      if (!p.active) continue

      p.x += p.speed * dt * (0.5 + rate)
      p.progress = (p.x - STATION_X[0]) / ((STATION_X[STATION_COUNT - 1] - STATION_X[0]) / (STATION_COUNT - 1))

      // Offload decision at station 4
      if (!p.fastPath && p.progress >= 4 && p.progress < 4.02) {
        // ~80% of simple flows get accelerated (mirrors real caches)
        if (Math.random() < 0.8) p.fastPath = true
      }

      // Fast-path packets jump from station 4 toward egress
      if (p.fastPath && p.progress >= 4.05) {
        p.x += p.speed * dt * 3.5 // accelerate down the bypass
      }

      // Queue dwell at QoS shaper (slow-path only)
      if (!p.fastPath && p.progress >= 6 && p.progress < 6.35) {
        p.speed *= 0.985 ** (dt * 60)
        maxQueue++
      }

      // Egress
      if (p.x > STATION_X[STATION_COUNT - 1] + 2) {
        p.active = false
      }
    }
    this.stats.queueDepth = maxQueue
  }

  private spawn(mode: 'mixed' | 'voiceHeavy' | 'bulkHeavy') {
    const slot = this.packets.find((p) => !p.active)
    if (!slot) return // pool exhausted; skip this tick (no allocation!)

    slot.active = true
    slot.x = STATION_X[0]
    slot.kind = randomKind(mode)
    slot.size = KIND_SIZE[slot.kind]
    slot.speed = slot.kind === 'bulk' ? 9 : slot.kind === 'control' ? 14 : 11
    slot.fastPath = false
    slot.progress = 0
    slot.traced = false
    slot.hash = shortHash()
    this.stats.spawnedTotal++

    // Control-plane packets are always complex → CPU slow path
    if (slot.kind === 'control') slot.fastPath = false
  }

  /** Find or mark one active packet as the traced one (Follow One Packet). */
  traceOne(): boolean {
    const candidate =
      this.packets.find((p) => p.traced && p.active) ??
      (() => {
        const p = this.packets.find((q) => q.active)
        if (p) p.traced = true
        return p
      })()
    return !!candidate
  }

  clearTraced() {
    for (const p of this.packets) p.traced = false
  }
}
