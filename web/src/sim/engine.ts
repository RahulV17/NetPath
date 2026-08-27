/**
 * NetPath engine â€” TypeScript port of the Python DataPathEngine.
 *
 * Owns: fixed-size packet pool (zero per-frame allocation), flow table,
 * ACL matching, HW offload decision + flow cache, QoS token-bucket policing.
 * `step(dt)` is the only mutator; UI samples via `sampleReadouts()`.
 */
import { classify } from './classifier'
import type {
  AclRule,
  FlowRecord,
  OffloadTarget,
  PacketEvent,
  PacketKind,
  StationReadout,
} from './types'

// â”€â”€ Tunables â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const MAX_PACKETS = 220
export const STATION_COUNT = 8
const FLOW_TABLE_LIMIT = 256
const HW_CACHE_SIZE = 64
const OFFLOAD_PROBABILITY = 0.8   // mirrors Python's ~80% hit rate
const BUCKET_BYTES_DEFAULT = 64 * 1024
const STARVED_BUCKET_BYTES = 4 * 1024
const STARVED_REFILL_RATE = 8_000 // bytes/sec while starved

export const STATION_NAMES = [
  'Ingress', 'Protocol Parser', 'ACL Filter', 'ML Classifier',
  'HW Offload Engine', 'L2/L3 Forwarder', 'QoS Shaper', 'Egress',
] as const

const HEADER_BY_STATION = [
  'Raw frame', 'Eth / VLAN / IP', '5-tuple match', 'Flow features Ã—8',
  'Profile + cache key', 'FIB / MAC lookup', 'DSCP mark / queue',
  'Tx-ready frame',
]

const KIND_SIZE_B: Record<PacketKind, number> = {
  voice: 200,
  video: 1200,
  bulk: 1400,
  control: 96,
}

export interface SimPacket {
  active: boolean
  /** Fractional station progress 0..7. */
  progress: number
  speed: number
  kind: PacketKind
  sizeB: number
  fastPath: boolean
  dropped: boolean
  dropStation: number | null
  traced: boolean
  hash: string
  iatSec: number
  bornAtSec: number
}

export interface EngineStats {
  processed: number
  dropped: number
  fastPathCount: number
  slowPathCount: number
  queueDepth: number
  hwCacheUsed: number
  activeFlows: number
}

function shortHash(): string {
  // 64-bit hash space (backend uses SHA-256[:16]) — 16 bits merged
  // unrelated flows and granted false HW-cache hits.
  return Array.from(
    { length: 4 },
    () => Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0'),
  ).join('').slice(0, 16)
}

function makePool(): SimPacket[] {
  return Array.from({ length: MAX_PACKETS }, () => ({
    active: false, progress: 0, speed: 0,
    kind: 'control' as PacketKind, sizeB: 96,
    fastPath: false, dropped: false, dropStation: null,
    traced: false, hash: '', iatSec: 0, bornAtSec: 0,
  }))
}

export type TrafficMode = 'mixed' | 'voiceHeavy' | 'bulkHeavy'

/**
 * Packet-kind picker combining protocol mix with the size-distribution
 * slider. distNorm 0..1: 0 skews small frames (voice/control), 1 skews
 * large frames (video/bulk). Weights lerp between the two extremes so
 * the slider visibly changes what flies through the pipeline.
 */
function pickKind(mode: TrafficMode, distNorm = 0.5): PacketKind {
  const r = Math.random()
  // Base profiles per protocol mix
  const base =
    mode === 'voiceHeavy'
      ? [0.65, 0.2, 0.15]   // voice, video, bulk
      : mode === 'bulkHeavy'
        ? [0.25, 0.15, 0.6]
        : [0.3, 0.4, 0.3]   // mixed
  // Size skew: small-heavy vs large-heavy endpoints
  const skew = distNorm - 0.5 // -0.5..+0.5
  const voice = Math.max(0.05, Math.min(0.9, base[0] - skew * 0.7))
  const bulk = Math.max(0.05, Math.min(0.9, base[2] + skew * 0.7))
  const video = Math.max(0.05, 1 - voice - bulk)

  // Small control-plane share (DHCP/ARP-style) — previously unreachable,
  // leaving the entire control-plane slow-path machinery dead code.
  const control = 0.06

  if (r < control) return 'control'
  const adjusted = (r - control) / (1 - control)
  if (adjusted < voice) return 'voice'
  if (adjusted < voice + video) return 'video'
  return 'bulk'
}

export class NetPathEngine {
  packets: SimPacket[] = makePool()
  stats: EngineStats = {
    processed: 0, dropped: 0, fastPathCount: 0, slowPathCount: 0,
    queueDepth: 0, hwCacheUsed: 0, activeFlows: 0,
  }

  private flows = new Map<string, FlowRecord>()
  private aclRules: AclRule[] = NetPathEngine.defaultAclRules()
  private hwCache = new Set<string>()
  private tokens = BUCKET_BYTES_DEFAULT
  private lastTokenRefill = 0
  private spawnDebt = 0

  /** Default ruleset â€” baseline drops give Chapter 2 real behavior. */
  static defaultAclRules(): AclRule[] {
    return [
      // Baseline: block FTP bulk transfers (visible drops at Station 2)
      { src: '*', dst: 'any', sport: -1, dport: 21, proto: 6, action: 'drop' },
      { src: '*', dst: '*', sport: -1, dport: -1, proto: -1, action: 'allow' },
    ]
  }

  setAclRules(rules: AclRule[]) {
    this.aclRules = rules
  }

  /** Surgical restore â€” keeps live packets and stats intact. */
  restoreDefaultAcls(): void {
    this.aclRules = NetPathEngine.defaultAclRules()
  }

  // â”€â”€ Fault-challenge injection points (spec Â§4 challenges) â”€â”€

  /** ACL Misconfiguration: block everything at Station 2. */
  injectAclMisconfiguration(): void {
    this.setAclRules([
      { src: '*', dst: '*', sport: -1, dport: -1, proto: -1, action: 'drop' },
    ])
  }

  /** Classifier Drift: force every flow to mislabel as VOICE. */
  injectClassifierDrift(): void {
    for (const f of this.flows.values()) f.trafficClass = 'VOICE'
    this.driftActive = true
  }

  driftActive = false

  clearDrift(): void {
    this.driftActive = false
    for (const f of this.flows.values()) this.classifyFlow(f)
    // Cold flows (<5 samples) keep the VOICE poison since classifyFlow
    // early-returns on them — reset explicitly.
    for (const f of this.flows.values()) {
      if (f.sizes.length < 5) f.trafficClass = 'BEST'
    }
  }

  /** Offload Cache Overflow: fill the HW cache so all flows hit CPU path. */
  injectOffloadOverflow(): void {
    while (this.hwCache.size < HW_CACHE_SIZE) {
      this.hwCache.add(shortHash() + shortHash())
    }
  }

  hwCacheClear(): void {
    this.hwCache.clear()
  }

  /** QoS Starvation: shrink the token bucket so bulk traffic gets dropped. */
  qosStarvationActive = false

  injectQosStarvation(): void {
    this.qosStarvationActive = true
    this.tokens = STARVED_BUCKET_BYTES // tiny bucket â€” policing drops most packets
  }

  clearQosStarvation(): void {
    this.qosStarvationActive = false
    this.tokens = BUCKET_BYTES_DEFAULT
  }

  // First-match evaluation: a matching allow rule terminates immediately —
  // a later catch-all drop must not override explicit permits.
  private aclDrops(kind: PacketKind, dport: number): boolean {
    for (const rule of this.aclRules) {
      if (rule.proto !== -1 && !this.kindProto(kind).includes(rule.proto)) continue
      if (rule.dport !== -1 && rule.dport !== dport) continue
      if (rule.src !== '*' && rule.src !== 'any') continue
      if (rule.dst !== '*' && rule.dst !== 'any') continue
      return rule.action === 'drop'
    }
    return false
  }

  private kindProto(kind: PacketKind): number[] {
    return kind === 'control' ? [17, 67] : [6, 17]
  }

  // â”€â”€ Flow table with expiry (mirrors Python FlowTable) â”€â”€
  private touchFlow(p: SimPacket, now: number): FlowRecord {
    let f = this.flows.get(p.hash)
    if (!f) {
      if (this.flows.size >= FLOW_TABLE_LIMIT) {
        // expire oldest
        let oldestKey = ''
        let oldest = Infinity
        for (const [k, v] of this.flows) {
          if (v.lastSeen < oldest) { oldest = v.lastSeen; oldestKey = k }
        }
        this.flows.delete(oldestKey)
      }
      f = {
        hash: p.hash, kind: p.kind, trafficClass: 'BEST',
        sizes: [], interArrivals: [], totalBytes: 0, totalPackets: 0,
        lastSeen: now, fastPath: false,
      }
      this.flows.set(p.hash, f)
    }
    const sizes = f.sizes
    sizes.push(p.sizeB)
    if (sizes.length > 100) sizes.shift()
    if (f.totalPackets > 0) {
      f.interArrivals.push(now - f.lastSeen)
      if (f.interArrivals.length > 100) f.interArrivals.shift()
    }
    f.totalBytes += p.sizeB
    f.totalPackets++
    f.lastSeen = now
    return f
  }

  classifyFlow(f: FlowRecord): void {
    // During classifier-drift challenge, labels are poisoned and must
    // NOT be recomputed â€” otherwise drift resets within one packet.
    if (this.driftActive) {
      f.trafficClass = 'VOICE'
      return
    }
    if (f.sizes.length < 5) return
    const meanSize =
      f.sizes.reduce((a, b) => a + b, 0) / (f.sizes.length || 1)
    const m = f.interArrivals.length || 1
    const meanIat = f.interArrivals.reduce((a, b) => a + b, 0) / m
    let varIat = 0
    for (const t of f.interArrivals) varIat += (t - meanIat) ** 2
    const jitterMs = Math.sqrt(varIat / m) * 1000
    const pps = meanIat > 0 ? 1 / meanIat : 0

    f.trafficClass = classify(
      { meanSize, stdSize: 50, meanIatMs: meanIat * 1000, stdIatMs: jitterMs, pps },
      f.kind,
    )
  }

  // â”€â”€ HW offload decision (mirrors OffloadEngine.decide_offload) â”€â”€
  private decideOffload(p: SimPacket, hash: string): OffloadTarget {
    const complex = p.kind === 'control'
    if (complex) {
      this.stats.slowPathCount++
      return 'CPU slow path'
    }
    if (this.hwCache.has(hash)) {
      this.stats.fastPathCount++
      return p.kind === 'bulk' ? 'HW QoS' : 'HW NIC'
    }
    if (this.hwCache.size >= HW_CACHE_SIZE) {
      return 'CPU fast path'
    }
    if (Math.random() < OFFLOAD_PROBABILITY) {
      this.hwCache.add(hash)
      this.stats.fastPathCount++
      return p.kind === 'bulk' ? 'HW QoS' : 'HW NIC'
    }
    this.stats.slowPathCount++
    return 'CPU slow path'
  }

  // â”€â”€ QoS token bucket policing (mirrors TokenBucket.consume) â”€â”€
  private police(bytes: number, now: number): boolean {
    if (now > this.lastTokenRefill) {
      const elapsed = now - this.lastTokenRefill
      // During starvation challenge the bucket stays tiny â€” refill is
      // capped at the starved size so policing keeps dropping packets.
      const cap = this.qosStarvationActive ? STARVED_BUCKET_BYTES : BUCKET_BYTES_DEFAULT
      this.tokens = Math.min(
        cap,
        this.tokens + elapsed * (this.qosStarvationActive ? STARVED_REFILL_RATE : 250_000),
      )
      this.lastTokenRefill = now
    }
    if (this.tokens >= bytes) {
      this.tokens -= bytes
      return true
    }
    return false
  }

  // â”€â”€ Main step â”€â”€
  step(
    dt: number,
    running: boolean,
    rateNorm: number,
    mode: TrafficMode,
    sizeDist = 0.5,
  ) {
    const now = performance.now() / 1000

    // spawn
    if (running) {
      this.spawnDebt += dt * (2 + rateNorm * 38)
      while (this.spawnDebt >= 1) {
        this.spawnDebt -= 1
        this.spawn(mode, sizeDist, now)
      }
    } else if (!this.packets.some((p) => p.active)) {
      this.stats.queueDepth = 0
      return
    }

    let queueDepth = 0
    for (const p of this.packets) {
      if (!p.active) continue

      p.progress += p.speed * dt * (0.55 + rateNorm)

      // Station events
      const st = Math.floor(p.progress)

      if (st === 2 && !(p as any)._aclChecked) {
        ;(p as any)._aclChecked = true
        if (this.aclDrops(p.kind, this.kindDport(p))) {
          p.dropped = true
          p.dropStation = 2
          p.active = false
          this.stats.dropped++
          continue
        }
      }

      if (st === 3 && !(p as any)._classified) {
        ;(p as any)._classified = true
        const f = this.touchFlow(p, now)
        this.classifyFlow(f)
      }

      if (st === 4 && !(p as any)._offloadDecided) {
        ;(p as any)._offloadDecided = true
        const target = this.decideOffload(p, p.hash)
        // Only true HW offload takes the bypass arc; CPU fast path still
        // traverses stations 5-6 (audit: overflow challenge previously
        // let packets keep flying the arc).
        p.fastPath =
          target === 'HW NIC' || target === 'HW QoS' ||
          target === 'HW WiFi' || target === 'HW crypto'
      }

      // Fast path bypasses stations 5â€“6
      if (p.fastPath && p.progress >= 4.05 && p.progress < 7) {
        p.progress += dt * p.speed * 0.9
      }

      // Queue dwell at QoS for slow-path packets
      if (!p.fastPath && p.progress >= 6 && p.progress < 6.35) {
        queueDepth++
      }

      // Egress / policing at station 6 exit for slow-path
      if (!p.fastPath && st === 6 && !(p as any)._policed) {
        ;(p as any)._policed = true
        if (!this.police(p.sizeB, now)) {
          p.dropped = true
          p.dropStation = 6
          p.active = false
          this.stats.dropped++
          continue
        }
      }

      if (p.progress >= STATION_COUNT - 1 + 0.5) {
        p.active = false
        this.stats.processed++
      }
    }

    this.stats.queueDepth = queueDepth
    this.stats.hwCacheUsed = this.hwCache.size
    this.stats.activeFlows = this.flows.size
  }

  private kindDport(p: SimPacket): number {
    switch (p.kind) {
      case 'control': return 67   // DHCP-like
      case 'voice': return 5060   // SIP
      case 'video': return 443    // HTTPS
      case 'bulk': return 21      // FTP
    }
  }

  private spawn(mode: TrafficMode, sizeDist = 0.5, bornAtSec: number) {
    const slot = this.packets.find((q) => !q.active)
    if (!slot) return
    slot.active = true
    slot.progress = 0
    slot.kind = pickKind(mode, sizeDist)
    slot.sizeB = KIND_SIZE_B[slot.kind]
    slot.speed = slot.kind === 'bulk' ? 3.2 : slot.kind === 'control' ? 5 : 4
    slot.fastPath = false
    slot.dropped = false
    slot.dropStation = null
    slot.traced = false
    slot.hash = shortHash()
    slot.bornAtSec = bornAtSec
    ;(slot as any)._aclChecked = false
    ;(slot as any)._classified = false
    ;(slot as any)._offloadDecided = false
    ;(slot as any)._policed = false
  }

  traceOne(): boolean {
    const existing = this.packets.find((p) => p.traced && p.active)
    if (existing) return true
    const candidate = this.packets.find((p) => p.active)
    if (!candidate) return false
    candidate.traced = true
    return true
  }

  clearTraced(): void {
    for (const p of this.packets) p.traced = false
  }

  reset(): void {
    for (const p of this.packets) {
      p.active = false
      p.traced = false
    }
    this.flows.clear()
    this.hwCache.clear()
    this.aclRules = NetPathEngine.defaultAclRules()
    this.driftActive = false
    this.qosStarvationActive = false
    this.tokens = BUCKET_BYTES_DEFAULT
    this.stats = {
      processed: 0, dropped: 0, fastPathCount: 0, slowPathCount: 0,
      queueDepth: 0, hwCacheUsed: 0, activeFlows: 0,
    }
  }

  /** Throttled readouts for the Station panel (called ~4 Hz). */
  sampleReadouts(): StationReadout[] {
    const running = this.packets.some((p) => p.active)
    return STATION_NAMES.map((_name: string, i: number) => {
      if (!running) {
        return {
          packetSize: '-', headerType: HEADER_BY_STATION[i], flowHash: '-',
          offloadTarget: '-', trafficClass: '-', queueDepth: 0, latencyMs: 0,
        }
      }
      const base = 120 + i * 38
      return {
        packetSize: `${(64 + ((i * 137) % 900)).toFixed(0)} B`,
        headerType: HEADER_BY_STATION[i],
        flowHash: (0x1000 + ((i * 2654435761) % 0xefff)).toString(16).padStart(4, '0'),
        offloadTarget:
          i < 4 ? '-'
          : i === 4 ? `${this.stats.hwCacheUsed} cached`
          : i === 7 ? 'NIC Tx'
          : this.stats.fastPathCount > this.stats.slowPathCount ? 'HW NIC' : 'CPU fast path',
        trafficClass:
          i < 3 ? '-'
          : ['VOICE', 'VIDEO', 'BULK', 'BEST'][i % 4],
        queueDepth: i === 6 ? this.stats.queueDepth : 0,
        latencyMs: Number((base + this.stats.queueDepth * 12).toFixed(1)),
      }
    })
  }

  get selectedPacketEvent(): PacketEvent | null {
    const p = this.packets.find((q) => q.traced && q.active)
    if (!p) return null
    const st = Math.min(7, Math.floor(p.progress))
    const now = performance.now() / 1000
    const elapsedMs = p.bornAtSec ? Number(((now - p.bornAtSec) * 1000).toFixed(1)) : 0
    return {
      station: st,
      headerType: HEADER_BY_STATION[st],
      flowHash: p.hash,
      offloadTarget: p.fastPath ? 'HW NIC' : st >= 4 ? 'CPU fast path' : null,
      trafficClass: st >= 3 ? 'VOICE' : null,
      queueDepth: st === 6 ? this.stats.queueDepth : 0,
      latencyMs: elapsedMs,
      packetSizeB: p.sizeB,
    }
  }
}
