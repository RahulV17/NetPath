/** Shared scene constants — re-exported so components avoid deep imports. */
export { MAX_PACKETS, STATION_COUNT, STATION_NAMES } from './engine'
export type { NetPathEngine } from './engine'

import * as THREE from 'three'
import type { PacketKind } from './types'

export const STATION_X = Array.from(
  { length: 8 },
  (_, i) => -31 + i * (62 / 7),
)

export const KIND_COLOR: Record<PacketKind, string> = {
  voice: '#f2c45f',
  video: '#6fc7e8',
  bulk: '#b08d57',
  control: '#e8e0cc',
}

// Keep THREE referenced for consumers that import from here
export { THREE }
