/**
 * Live Engine bridge — attaches the lab to the real Python backend.
 *
 * Subscribes to FastAPI's `/ws/live` WebSocket (pushed every 100 ms) and
 * maps backend telemetry onto the lab's StationReadout format. Falls back
 * gracefully when the backend isn't running.
 */
import type { StationReadout } from '../sim/types'

const WS_URL = 'ws://localhost:8000/ws/live'

export interface BackendSnapshot {
  timestamp: number
  datapath: {
    packets_processed?: number
    packets_dropped?: number
    packets_bridged?: number
    packets_routed?: number
    packets_trapped?: number
    avg_latency_us?: number
  }
  qos: {
    classified?: number
    shaped?: number
    policed?: number
    queue_depths?: Record<string, number>
  }
  analytics: {
    protocol_distribution?: Record<string, number>
    throughput?: { pps?: number; total_bytes?: number; bytes_per_second?: number }
  }
  ml?: { flow_table_size?: number }
  hw_offload?: {
    hw_accelerated?: number
    cpu_exceptions?: number
    hw_cache_utilization?: number
    hw_offload_rate?: number
  } | null
  /** Real per-flow telemetry (added by FlowTable.snapshot). */
  flows?: {
    count?: number
    classified?: Record<string, number>
    top?: Array<{
      src: string
      dst: string
      proto: number
      packets: number
      bytes: number
      age_sec: number
      avg_size: number
      class: string
    }>
  }
}

/** Dominant ML class from the backend's classified-flow histogram. */
export function dominantClass(
  classified?: Record<string, number>,
): string | null {
  if (!classified) return null
  let best: string | null = null
  let n = 0
  for (const [k, v] of Object.entries(classified)) {
    if (k === 'UNCLASSIFIED') continue
    if (v > n) {
      best = k
      n = v
    }
  }
  return best
}

const HEADERS = [
  'Raw frame', 'Eth / VLAN / IP', '5-tuple match', 'Flow features ×8',
  'Profile + cache key', 'FIB / MAC lookup', 'DSCP mark / queue',
  'Tx-ready frame',
]

export function mapToReadouts(snap: BackendSnapshot): StationReadout[] {
  const dp = snap.datapath ?? {}
  const hw = snap.hw_offload ?? undefined
  const qos = snap.qos ?? {}
  const latency = dp.avg_latency_us ?? 0
  const offloadRate = hw?.hw_offload_rate ?? 0

  // Real values where the backend provides them; honest '—' otherwise.
  const realQueue = Object.values(qos.queue_depths ?? {}).reduce(
    (a: number, b) => a + (Number(b) || 0),
    0,
  )
  const topFlow = snap.flows?.top?.[0]
  const domClass = dominantClass(snap.flows?.classified)

  return HEADERS.map((header, i) => ({
    packetSize:
      i === 1 && topFlow ? `${topFlow.avg_size} B` : '—',
    headerType: header,
    flowHash:
      i >= 3 && topFlow
        ? `#${topFlow.src.split(':').pop()}/${topFlow.dst.split(':').pop()}`
        : '—',
    offloadTarget:
      i < 4 ? '—'
      : i === 4 ? `${Math.round((offloadRate || 0) * 100)}% HW`
      : i === 7 ? 'NIC Tx'
      : (offloadRate > 0.5 ? 'HW NIC' : 'CPU fast path'),
    trafficClass:
      i === 3 && domClass ? domClass
      : i > 3 && domClass ? domClass
      : i >= 3 ? '—'
      : '—',
    queueDepth: i === 6 ? realQueue : 0,
    latencyMs: Number((latency / 1000).toFixed(1)),
  }))
}

/** Minimal reconnecting WebSocket wrapper for live telemetry. */
export class LiveEngineClient {
  private ws: WebSocket | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private connectTimer: ReturnType<typeof setTimeout> | null = null

  constructor(
    private onSnapshot: (snap: BackendSnapshot) => void,
    private onStatus: (connected: boolean) => void,
  ) {}

  connect() {
    if (this.ws) return
    try {
      this.ws = new WebSocket(WS_URL)
    } catch {
      this.scheduleRetry()
      return
    }

    // If the handshake never completes, treat it as a failed connect
    // instead of leaving the UI permanently on "connecting…".
    this.connectTimer = setTimeout(() => {
      this.connectTimer = null
      if (this.ws && this.ws.readyState !== WebSocket.OPEN) {
        try { this.ws.close() } catch { /* ignore */ }
      }
    }, 3000)

    this.ws.onopen = () => {
      if (this.connectTimer) { clearTimeout(this.connectTimer); this.connectTimer = null }
      this.onStatus(true)
    }
    this.ws.onclose = () => {
      if (this.connectTimer) { clearTimeout(this.connectTimer); this.connectTimer = null }
      this.ws = null
      this.scheduleRetry()
    }
    this.ws.onerror = () => {
      // onclose follows onerror; if it doesn't, ensure retry eventually.
      if (!this.connectTimer) this.scheduleRetry()
    }
    this.ws.onmessage = (ev) => {
      if (typeof ev.data !== 'string') return
      try {
        this.onSnapshot(JSON.parse(ev.data))
      } catch { /* ignore malformed frames */ }
    }
  }

  private scheduleRetry() {
    if (!this.retryTimer) {
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null
        this.connect()
      }, 3000)
    }
  }

  disconnect() {
    if (this.retryTimer) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
    if (this.ws) {
      this.ws.onclose = null // prevent reconnect loop on manual close
      this.ws.close()
      this.ws = null
    }
    this.onStatus(false)
  }
}
