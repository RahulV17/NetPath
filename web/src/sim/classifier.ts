/**
 * Heuristic traffic classifier — TS approximation of the Python
 * Random Forest. Real sklearn cannot run in the browser; the spec (§5)
 * explicitly allows qualitative "representative trends", so this uses
 * hand-tuned thresholds on the same 8 features:
 *
 *   mean/std/min/max size · mean/std inter-arrival (jitter) · pps
 *
 * Voice: small packets, very regular spacing, low jitter.
 * Video: large packets, bursty spacing, moderate jitter.
 * Bulk:  largest packets, tight spacing, high throughput.
 * Interactive: small-medium packets, irregular bursts.
 */
import type { PacketKind, TrafficClass } from './types'

interface Features {
  meanSize: number
  stdSize: number
  meanIatMs: number
  stdIatMs: number // jitter
  pps: number
}

export function extractFeatures(
  sizes: number[],
  interArrivals: number[],
): Features {
  const n = sizes.length || 1
  const meanSize = sizes.reduce((a, b) => a + b, 0) / n

  let varSize = 0
  for (const s of sizes) varSize += (s - meanSize) ** 2
  const stdSize = Math.sqrt(varSize / n)

  const m = interArrivals.length || 1
  const meanIat = interArrivals.reduce((a, b) => a + b, 0) / m
  let varIat = 0
  for (const t of interArrivals) varIat += (t - meanIat) ** 2
  const stdIat = Math.sqrt(varIat / m)

  return {
    meanSize,
    stdSize,
    meanIatMs: meanIat * 1000,
    stdIatMs: stdIat * 1000,
    pps: meanIat > 0 ? 1 / meanIat : 0,
  }
}

/**
 * Classify from features + packet kind prior.
 * Ordered rules mimic decision-tree traversal; thresholds chosen so the
 * three archetypes from the Python seed data classify correctly:
 *   voice ≈ 200 B @ 20 ms   video ≈ 1200 B bursty   bulk ≈ 1400 B tight
 */
export function classify(f: Features, kind: PacketKind): TrafficClass {
  if (kind === 'control') return 'BEST'

  if (f.meanSize < 320 && f.stdIatMs < 8 && f.meanIatMs > 4) {
    return 'VOICE'                       // small, regular, low jitter
  }
  if (f.meanSize > 1250 && f.meanIatMs < 3) {
    return 'BULK'                        // huge packets, back-to-back
  }
  if (f.meanSize > 800 && f.stdIatMs >= 3) {
    return 'VIDEO'                       // large, bursty
  }
  if (f.stdIatMs > 15 && f.meanSize < 600) {
    return 'INTERACTIVE'                 // irregular small bursts
  }
  return 'BEST'
}
