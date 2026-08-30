/**
 * LabPage — the NetPath interactive experience.
 * Owns the engine instance and the rAF-driven simulation loop.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLab } from './store'
import { NetPathEngine, STATION_NAMES } from '../sim/engine'
import { PipelineScene } from './PipelineScene'
import { WebglFallback } from './WebglFallback'
import { ControlPanel } from './ControlPanel'
import { StationReadout, Disclaimer } from './StationReadout'
import { ChallengeModal } from './ChallengeModal'
import { setMuted, disposeAudio, sfx } from './audio'
import {
  useIsMobile,
  MobileBars,
  DesktopHint,
} from './MobileLayer'

function webglAvailable(): boolean {
  try {
    const c = document.createElement('canvas')
    return !!(c.getContext('webgl2') || c.getContext('webgl'))
  } catch {
    return false
  }
}

// ── Chapters (spec §4 copy) ──────────────────────────────────────────────

const CHAPTERS = [
  { q: 'Why must packets be parsed before forwarding?', tip: 'Start the data path and watch how headers are decoded at each layer.' },
  { q: 'How does the ACL decide which packets to drop?', tip: 'Watch packets stop at Station 2 when a rule matches.' },
  { q: 'What does the ML classifier learn from flow features?', tip: 'Station 3 labels flows as voice, video, bulk or best effort.' },
  { q: 'When does hardware take over from the CPU?', tip: 'Green arcs at Station 4 mark fast-path offloads.' },
  { q: 'How does QoS guarantee bandwidth for voice traffic?', tip: 'Queue depths at Station 6 favor small urgent packets.' },
  { q: 'Follow a packet through the complete data path.', tip: 'Enable Follow One Packet and trace it end to end.' },
]

function Header() {
  const chapter = useLab((s) => s.chapter)
  const setChapter = useLab((s) => s.setChapter)
  const ch = CHAPTERS[chapter]

  return (
    <header className="pointer-events-none absolute inset-x-0 top-0 z-20 px-8 pt-5">
      <div className="flex items-start justify-between gap-6">
        <div>
          <div className="font-mono text-[10px] tracking-[0.3em] text-[#b08d57]">
            THE PACKET ENGINE No. 01
          </div>
          <h1 className="mt-1 font-display text-2xl text-[#e8e0cc]">
            NetPath · Network Data Path Lab
          </h1>
          <p className="mt-1 max-w-xl text-xs leading-relaxed text-[#9d978a]">
            Trace a network packet from ingress to egress through a modern
            high-performance data path.
          </p>
        </div>
        <div className="max-w-sm text-right" aria-live="polite">
          <div className="font-mono text-[10px] tracking-widest text-[#9d978a]">
            CHAPTER {chapter + 1} OF 6
          </div>
          <p className="mt-1 font-display text-sm leading-snug text-[#e8e0cc]">{ch.q}</p>
          <p className="mt-1 text-[11px] text-[#9d978a]">{ch.tip}</p>
          <div className="mt-2 flex justify-end gap-2">
            <button
              onClick={() => setChapter(chapter - 1)}
              disabled={chapter === 0}
              aria-label="Previous Chapter"
              className="rounded border border-[#30363d] px-2 py-1 font-mono text-[10px] text-bone disabled:opacity-30 hover:border-nickel focus-visible:border-cyan"
            >
              PREV
            </button>
            <button
              onClick={() => setChapter(chapter + 1)}
              disabled={chapter === 5}
              aria-label="Next Chapter"
              className="rounded border border-[#30363d] px-2 py-1 font-mono text-[10px] text-bone disabled:opacity-30 hover:border-nickel focus-visible:border-cyan"
            >
              NEXT
            </button>
          </div>
        </div>
      </div>
    </header>
  )
}

// ── Simulation driver (rAF loop outside React render) ────────────────────

function SimDriver({ engine }: { engine: NetPathEngine }) {
  const lastPush = useRef(0)
  const last = useRef(performance.now())

  useEffect(() => {
    let raf = 0
    let running = true

    const tick = () => {
      if (!running) return
      raf = requestAnimationFrame(tick)

      // Pause when tab hidden (spec §11)
      if (document.hidden) {
        last.current = performance.now()
        return
      }

      const now = performance.now()
      const dt = Math.min((now - last.current) / 1000, 0.05)
      last.current = now

      const s = useLab.getState()
      const isRunning =
        s.pathState !== 'Idle' && s.pathState !== 'Stopping'

      const prevProcessed = engine.stats.processed
      const prevDropped = engine.stats.dropped
        engine.step(dt, isRunning, s.packetRate / 100, s.trafficMode, s.sizeDist / 100)

      // Audio cues — only while sound is enabled (spec: subtle, not HUD-y)
      if (s.soundEnabled) {
        if (engine.stats.processed > prevProcessed + 4) sfx.offload()
        if (engine.stats.dropped > prevDropped) sfx.drop()
      }

      // State machine transitions on stop / drain
      if (s.pathState === 'Starting') {
        useLab.setState({ pathState: 'Parsing' })
        if (s.soundEnabled) sfx.start()
      } else if (s.pathState === 'Stopping') {
        if (!engine.packets.some((p) => p.active)) {
          useLab.setState({ pathState: 'Idle' })
        }
      }

      // Throttled readout push (~4 Hz) — never per frame.
      // While the Live Engine bridge is attached+connected, backend
      // snapshots (~10 Hz) own the readouts; local sim must not clobber.
      const liveOwnsReadouts =
        useLab.getState().liveEngineStatus === 'live'
      if (now - lastPush.current > 250 && isRunning && !liveOwnsReadouts) {
        lastPush.current = now
        useLab.getState().pushReadouts(engine.sampleReadouts())
        useLab.setState({
          throughputGbps: Number(((s.packetRate / 100) * 1.5).toFixed(2)),
          activeFlows: engine.stats.activeFlows,
        })
      }
    }

    raf = requestAnimationFrame(tick)
    return () => {
      running = false
      cancelAnimationFrame(raf)
    }
  }, [engine])

  return null
}

// ── Help chip ──────────────────────────────────────────────────────────────

function HelpChip() {
  const [open, setOpen] = useState(false)
  return (
    <div className="fixed bottom-3 left-3 z-50">
      {open && (
        <div className="mb-2 rounded-md border border-[#1c2530] bg-[#0b0f14]/95 px-3 py-2 text-xs text-[#9d978a] shadow-lg">
          <div className="mb-1 font-mono text-[#e8e0cc]">Controls</div>
          <div>Drag to orbit</div>
          <div>Scroll to zoom</div>
          <div>H toggles side panels</div>
        </div>
      )}
      <button
        onClick={() => setOpen((v) => !v)}
        className="rounded-full border border-[#1c2530] bg-[#0b0f14]/80 px-2 py-1 font-mono text-xs text-[#e8e0cc] hover:border-[#f2c45f]"
      >
        ?
      </button>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────

/**
 * Desktop-only collapsible panel shell. On desktop (md+) it renders the
 * child panel; when collapsed it shrinks to a thin edge rail with a single
 * expand button so the 3D canvas reclaims the full width. On mobile it always
 * renders the child (the bottom drawers handle show/hide there).
 */
function DesktopPanelSide({ position, children }: {
  position: 'left' | 'right'
  children: React.ReactNode
}) {
  const isMobile = useIsMobile()
  const collapsed =
    position === 'left'
      ? useLab((s) => s.desktopControlCollapsed)
      : useLab((s) => s.desktopReadoutCollapsed)
  const toggle =
    position === 'left'
      ? useLab((s) => s.toggleDesktopControl)
      : useLab((s) => s.toggleDesktopReadout)

  if (isMobile) return <>{children}</>

  const collapsedCls =
    position === 'left'
      ? 'left-3 top-28 z-20'
      : 'right-3 top-28 z-20'
  const expandedWrapperCls =
    position === 'left'
      ? 'absolute bottom-4 left-4 top-28 z-20'
      : 'absolute right-4 top-28 z-20'

  if (collapsed) {
    return (
      <button
        onClick={toggle}
        aria-label={position === 'left' ? 'Expand controls' : 'Expand station data'}
        aria-expanded={false}
        className={`pointer-events-auto absolute ${collapsedCls} flex h-10 w-10 items-center justify-center rounded-lg border border-[#30363d] bg-[rgba(7,9,12,0.84)] font-mono text-sm text-[#9d978a] backdrop-blur transition-colors hover:border-[#b08d57] hover:text-[#e8e0cc]`}
      >
        {position === 'left' ? '›' : '‹'}
      </button>
    )
  }

  return (
    <div className={expandedWrapperCls}>
      <div className="relative h-full">
        {children}
        <button
          onClick={toggle}
          aria-label={position === 'left' ? 'Collapse controls' : 'Collapse station data'}
          aria-expanded={true}
          className={`pointer-events-auto absolute -top-0 ${
            position === 'left' ? '-right-3' : '-left-3'
          } flex h-8 w-8 items-center justify-center rounded-full border border-[#30363d] bg-[rgba(7,9,12,0.92)] font-mono text-xs text-[#9d978a] backdrop-blur transition-colors hover:border-[#b08d57] hover:text-[#e8e0cc]`}
        >
          {position === 'left' ? '‹' : '›'}
        </button>
      </div>
    </div>
  )
}

export default function LabPage() {
  const hasWebgl = useMemo(webglAvailable, [])
  const engine = useMemo(() => new NetPathEngine(), [])
  const [challengeOpen, setChallengeOpen] = useState(false)
  const [challengeResults, setChallengeResults] = useState<
    Array<{ scenario: string; correct: boolean }>
  >([])
  const isMobile = useIsMobile()

  // Audio context lifecycle — resume/unmute via store, dispose on unmount
  const soundEnabled = useLab((s) => s.soundEnabled)
  useEffect(() => {
    setMuted(!soundEnabled)
  }, [soundEnabled])
  useEffect(() => () => disposeAudio(), [])

  // Keyboard shortcut: H toggles both desktop panels
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return
      if (e.key === 'h' || e.key === 'H') {
        const s = useLab.getState()
        s.toggleDesktopControl()
        s.toggleDesktopReadout()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const recordChallengeResult = (scenario: string, correct: boolean) => {
    setChallengeResults((prev) => [...prev.slice(-11), { scenario, correct }])
  }

  return (
    <div className="fixed inset-0 overflow-hidden bg-[#05070a] text-[#e8e0cc]">
      {hasWebgl ? <PipelineScene engine={engine} isMobile={isMobile} /> : <WebglFallback />}

      <Header />

      {hasWebgl && (
        <>
          <SimDriver engine={engine} />
          {/* Desktop-absolute panels — collapsible to reclaim 3D canvas space */}
          <DesktopPanelSide position="left">
            <ControlPanel
              engine={engine}
              challengeOpen={challengeOpen}
              onToggleChallenge={() => setChallengeOpen((v) => !v)}
              challengeResults={challengeResults}
            />
          </DesktopPanelSide>
          <DesktopPanelSide position="right">
            <StationReadout />
          </DesktopPanelSide>
          <ChallengeModal
            engine={engine}
            open={challengeOpen}
            onClose={() => setChallengeOpen(false)}
            onResult={recordChallengeResult}
          />
          {isMobile && <MobileBars />}
        </>
      )}
      {isMobile && <DesktopHint />}

      <Disclaimer />
      <HelpChip />
    </div>
  )
}

export { STATION_NAMES }
