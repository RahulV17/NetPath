/**
 * NetPath simulation types — shared across engine, scene, and UI.
 */

export type TrafficClass = 'VOICE' | 'VIDEO' | 'INTERACTIVE' | 'BULK' | 'BEST'

export type OffloadTarget =
  | 'CPU slow path'
  | 'CPU fast path'
  | 'HW NIC'
  | 'HW WiFi'
  | 'HW crypto'
  | 'HW QoS'

export type PacketKind = 'voice' | 'video' | 'bulk' | 'control'

/** Per-packet event emitted when it crosses a station. */
export interface PacketEvent {
  station: number
  headerType: string
  flowHash: string
  offloadTarget: OffloadTarget | null
  trafficClass: TrafficClass | null
  queueDepth: number
  latencyMs: number
  packetSizeB: number
}

/** Readout shape consumed by the StationReadout panel (spec §4). */
export interface StationReadout {
  packetSize: string
  headerType: string
  flowHash: string
  offloadTarget: string
  trafficClass: string
  queueDepth: number
  latencyMs: number
}

/** Flow record mirroring the Python FlowFeatures. */
export interface FlowRecord {
  hash: string
  kind: PacketKind
  trafficClass: TrafficClass
  sizes: number[]
  interArrivals: number[]
  totalBytes: number
  totalPackets: number
  lastSeen: number
  fastPath: boolean
}

export interface AclRule {
  src: string   // '*' wildcard allowed
  dst: string
  sport: number // -1 wildcard
  dport: number
  proto: number // -1 wildcard
  action: 'drop' | 'allow'
}
