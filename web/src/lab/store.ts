/**
 * Lab store — single source of truth for the 3D data path lab.
 *
 * English-only per spec §3. All state survives panel open/close (spec §6)
 * and orientation changes (spec §7). No per-frame values live here —
 * continuous animation state stays in refs inside the scene to avoid
 * re-rendering React at 60 fps.
 */
import { create } from 'zustand'

// ── Types ────────────────────────────────────────────────────────────────

export type PathState =
  | 'Idle'
  | 'Starting'
  | 'Parsing'
  | 'Classifying'
  | 'Offloading'
  | 'Forwarding'
  | 'Shaping'
  | 'Running'
  | 'Stopping'

export const STATIONS = [
  { id: 0, name: 'Ingress' },
  { id: 1, name: 'Protocol Parser' },
  { id: 2, name: 'ACL Filter' },
  { id: 3, name: 'ML Classifier' },
  { id: 4, name: 'HW Offload Engine' },
  { id: 5, name: 'L2/L3 Forwarder' },
  { id: 6, 'name': 'QoS Shaper' },
  { id: 7, name: 'Egress' },
] as const

export interface StationReadout {
  packetSize: string
  headerType: string
  flowHash: string
  offloadTarget: string
  trafficClass: string
  queueDepth: number
  latencyUs: number
}

export type CameraPreset =
  | 'overview'
  | 'section'
  | 'parser'
  | 'classifier'
  | 'offload'
  | 'free'

export type TrafficLayer =
  | 'headerLayers'
  | 'flowPaths'
  | 'queueDepth'
  | 'offloadDecisions'

export type TrafficMode = 'mixed' | 'voiceHeavy' | 'bulkHeavy'

interface LabState {
  // ── Data path control ──
  pathState: PathState
  packetRate: number        // 0..100 (%), normalized
  activeFlows: number       // normalized count
  throughputGbps: number    // display value, Approx.
  trafficMode: TrafficMode

  // ── Model ──
  exploded: number          // 0..1 slider
  sectionView: boolean
  pipelineOpacity: number   // 0.15..1

  // ── Traffic visualization layers ──
  layers: Record<TrafficLayer, boolean>

  // ── Camera / interaction ──
  cameraPreset: CameraPreset
  selectedStation: number   // 0..7

  // ── Learning ──
  chapter: number           // 0..5
  chaptersVisited: number[] // completion tracking for summary card
  soundEnabled: boolean

  // ── Mobile panels (spec §7 — mutual exclusion, mobile only) ──
  isControlPanelOpen: boolean
  isReadoutOpen: boolean
  isDesktopHintVisible: boolean

  // ── Live Engine bridge (attaches readouts to the Python backend) ──
  liveEngineAttached: boolean
  /** 'off' → not attached · 'connecting' → WS handshake/retry · 'live' → snapshots flowing */
  liveEngineStatus: 'off' | 'connecting' | 'live'
  /** Real values mirrored from the backend snapshot while attached. */
  liveFlowCount: number | null
  liveDominantClass: string | null
  /** Real packets/sec from the backend when Live Engine is attached. */
  livePps: number

  // ── Per-station readouts (updated on a throttled tick, not per frame) ──
  readouts: StationReadout[]

  // ── Actions ──
  start: () => void
  stop: () => void
  setPacketRate: (v: number) => void
  setActiveFlows: (v: number) => void
  setTrafficMode: (m: TrafficMode) => void
  /** User's flow-count slider (1..64) — never auto-overwritten by telemetry. */
  flowTarget: number
  setFlowTarget: (v: number) => void
  /** Packet size mix 0..100 — 0 skews small (voice/control), 100 large (video/bulk). */
  sizeDist: number
  setSizeDist: (v: number) => void
  setExploded: (v: number) => void
  toggleSectionView: () => void
  setPipelineOpacity: (v: number) => void
  toggleLayer: (l: TrafficLayer) => void
  setCameraPreset: (p: CameraPreset) => void
  selectStation: (i: number) => void
  setChapter: (c: number) => void
  toggleSound: () => void
  /** Mobile-only mutual exclusion (spec §7). No-ops on desktop. */
  openControlPanel: (isMobile: boolean) => void
  openReadout: (isMobile: boolean) => void
  dismissDesktopHint: () => void
  setLiveEngineAttached: (v: boolean) => void
  setLiveEngineStatus: (st: 'off' | 'connecting' | 'live') => void
  resetToDefault: () => void
  /** Throttled telemetry update from the simulation loop (~4 Hz). */
  pushReadouts: (r: StationReadout[]) => void
}

const DEFAULTS = {
  packetRate: 35,
  activeFlows: 12,
  flowTarget: 12,
  sizeDist: 50,
  trafficMode: 'mixed' as TrafficMode,
  exploded: 0,
  sectionView: false,
  pipelineOpacity: 1,
  layers: {
    headerLayers: true,
    flowPaths: true,
    queueDepth: true,
    offloadDecisions: true,
  } as Record<TrafficLayer, boolean>,
  cameraPreset: 'overview' as CameraPreset,
  selectedStation: 0,
  chapter: 0,
  chaptersVisited: [0],
  soundEnabled: false,
}

function makeReadouts(): StationReadout[] {
  return STATIONS.map(() => ({
    packetSize: '—',
    headerType: '—',
    flowHash: '—',
    offloadTarget: '—',
    trafficClass: '—',
    queueDepth: 0,
    latencyUs: 0,
  }))
}

export const useLab = create<LabState>((set, get) => ({
  pathState: 'Idle',
  ...DEFAULTS,
  throughputGbps: 0,
  readouts: makeReadouts(),
  isControlPanelOpen: false,
  isReadoutOpen: false,
  isDesktopHintVisible: true,
  liveEngineAttached: false,
  liveEngineStatus: 'off',
  livePps: 0,
  liveFlowCount: null,
  liveDominantClass: null,

  start: () => {
    if (get().pathState !== 'Idle') return
    set({ pathState: 'Starting' })
  },
  stop: () => {
    const s = get().pathState
    if (s === 'Idle') return
    set({ pathState: 'Stopping' })
  },
  setPacketRate: (v) => set({ packetRate: Math.max(0, Math.min(100, v)) }),
  setActiveFlows: (v) => set({ activeFlows: Math.max(0, Math.round(v)) }),
  setFlowTarget: (v) => set({ flowTarget: Math.max(1, Math.min(64, Math.round(v))) }),
  setSizeDist: (v) => set({ sizeDist: Math.max(0, Math.min(100, Math.round(v))) }),
  setTrafficMode: (m) => set({ trafficMode: m }),
  setExploded: (v) => set({ exploded: Math.max(0, Math.min(1, v)) }),
  toggleSectionView: () => set((s) => ({ sectionView: !s.sectionView })),
  setPipelineOpacity: (v) =>
    set({ pipelineOpacity: Math.max(0.15, Math.min(1, v)) }),
  toggleLayer: (l) =>
    set((s) => ({ layers: { ...s.layers, [l]: !s.layers[l] } })),
  setCameraPreset: (p) => set({ cameraPreset: p }),
  selectStation: (i) =>
    set({ selectedStation: Math.max(0, Math.min(7, i)) }),
  setChapter: (c) =>
    set((s) => {
      const clamped = Math.max(0, Math.min(5, c))
      return {
        chapter: clamped,
        chaptersVisited: s.chaptersVisited.includes(clamped)
          ? s.chaptersVisited
          : [...s.chaptersVisited, clamped],
      }
    }),
  toggleSound: () => set((s) => ({ soundEnabled: !s.soundEnabled })),
  openControlPanel: (isMobile) =>
    set((s) =>
      isMobile
        ? { isControlPanelOpen: true, isReadoutOpen: false } // mutual exclusion
        : { isControlPanelOpen: !s.isControlPanelOpen },
    ),
  openReadout: (isMobile) =>
    set((s) =>
      isMobile
        ? { isReadoutOpen: true, isControlPanelOpen: false }
        : { isReadoutOpen: !s.isReadoutOpen },
    ),
  dismissDesktopHint: () => {
    try {
      sessionStorage.setItem('netpath-hint-dismissed', '1')
    } catch { /* private mode */ }
    set({ isDesktopHintVisible: false })
  },
  setLiveEngineAttached: (v) =>
    set({ liveEngineAttached: v, liveEngineStatus: v ? 'connecting' : 'off' }),
  setLiveEngineStatus: (st) => set({ liveEngineStatus: st }),

  resetToDefault: () =>
    set((s) => ({
      ...DEFAULTS,
      layers: { ...DEFAULTS.layers },
      pathState: s.pathState, // controls like Reset keep run state; spec §6
      readouts: s.readouts,
      throughputGbps: s.throughputGbps,
    })),

  pushReadouts: (r) => set({ readouts: r }),
}))

/** Simulation loop reads this mutable mirror without subscribing React. */
export const simMirror = {
  get running() {
    return useLab.getState().pathState !== 'Idle' &&
           useLab.getState().pathState !== 'Stopping'
  },
}
