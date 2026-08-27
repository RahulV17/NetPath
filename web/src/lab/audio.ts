/**
 * NetPath audio — tiny synth blips behind a master mute.
 *
 * Browser policy: AudioContext must be created/resumed after a user
 * gesture. We lazily construct on first `setMuted(false)` and resume
 * on every subsequent unmute.
 */
let ctx: AudioContext | null = null
let master: GainNode | null = null

function ensureContext(): AudioContext | null {
  if (typeof window === 'undefined') return null
  if (!ctx) {
    const AC =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext
    if (!AC) return null
    ctx = new AC()
    master = ctx.createGain()
    master.gain.value = 0.12 // quiet — spec wants subtle, not HUD-y
    master.connect(ctx.destination)
  }
  return ctx
}

export function setMuted(muted: boolean): void {
  if (!ctx) {
    if (!muted) ensureContext()?.resume().catch(() => {})
    return
  }
  // Suspend fully when muted — guarantees silence even for in-flight blips
  if (muted) {
    ctx.suspend().catch(() => {})
  } else {
    ctx.resume().catch(() => {})
  }
}

interface BlipOpts {
  freq: number
  durationMs?: number
  type?: OscillatorType
  gainScale?: number
}

/** Fire-and-forget oscillator blip; no-ops while muted or before gesture. */
export function blip({
  freq,
  durationMs = 70,
  type = 'sine',
  gainScale = 1,
}: BlipOpts): void {
  if (!ctx || !master || ctx.state !== 'running') return
  const t0 = ctx.currentTime
  const osc = ctx.createOscillator()
  const g = ctx.createGain()
  osc.type = type
  osc.frequency.setValueAtTime(freq, t0)
  g.gain.setValueAtTime(0.0001, t0)
  g.gain.exponentialRampToValueAtTime(0.5 * gainScale, t0 + 0.008)
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + durationMs / 1000)
  osc.connect(g)
  g.connect(master)
  osc.start(t0)
  osc.stop(t0 + durationMs / 1000 + 0.02)
  // GC-friendly: nodes are one-shot and collected after stop
}

// ── Named events ─────────────────────────────────────────────────────────

export const sfx = {
  start: () => blip({ freq: 440, durationMs: 120, type: 'triangle' }),
  stationCross: () => blip({ freq: 660, durationMs: 40, gainScale: 0.5 }),
  drop: () => blip({ freq: 160, durationMs: 110, type: 'square', gainScale: 0.7 }),
  offload: () => blip({ freq: 880, durationMs: 55, gainScale: 0.6 }),
  complete: () => {
    blip({ freq: 523, durationMs: 90, type: 'triangle' })
    setTimeout(() => blip({ freq: 784, durationMs: 140, type: 'triangle' }), 90)
  },
}

/** Stop everything and release resources (called on unmount). */
export function disposeAudio(): void {
  ctx?.close().catch(() => {})
  ctx = null
  master = null
}
