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
  useDesktopPanelVisibility,
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
          <h1 className="mt-1 font-serif text-2xl text-[#e8e0cc]">
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
          <p className="mt-1 font-serif text-sm leading-snug text-[#e8e0cc]">{ch.q}</p>
          <p className="mt-1 text-[11px] text-[#9d978a]">{ch.tip}</p>
          <div className="mt-2 flex justify-end gap-2">
            <button
              onClick={() => setChapter(chapter - 1)}
              disabled={chapter === 0}
              aria-label="Previous Chapter"
              className="rounded border border-[#30363d] px-2 py-1 font-mono text-[10px] text-[#e8e0cc] disabled:opacity-30 hover:border-[#b08d57]"
            >
              PREV
            </button>
            <button
              onClick={() => setChapter(chapter + 1)}
              disabled={chapter === 5}
              aria-label="Next Chapter"
              className="rounded border border-[#30363d] px-2 py-1 font-mono text-[10px] text-[#e8e0cc] disabled:opacity-30 hover:border-[#b08d57]"
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

// ── Page ─────────────────────────────────────────────────────────────────

export default function LabPage() {
  const hasWebgl = useMemo(webglAvailable, [])
  const engine = useMemo(() => new NetPathEngine(), [])
  const [challengeOpen, setChallengeOpen] = useState(false)
  const [challengeResults, setChallengeResults] = useState<
    Array<{ scenario: string; correct: boolean }>
  >([])
  const isMobile = useIsMobile()
  const { hideControl, hideReadout } = useDesktopPanelVisibility()

  // Audio context lifecycle — resume/unmute via store, dispose on unmount
  const soundEnabled = useLab((s) => s.soundEnabled)
  useEffect(() => {
    setMuted(!soundEnabled)
  }, [soundEnabled])
  useEffect(() => () => disposeAudio(), [])

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
          {/* Desktop-absolute panels — hidden on mobile (drawers replace them) */}
          <div className={hideControl ? 'hidden md:block' : ''}>
            <ControlPanel
              engine={engine}
              challengeOpen={challengeOpen}
              onToggleChallenge={() => setChallengeOpen((v) => !v)}
              challengeResults={challengeResults}
            />
          </div>
          <div className={hideReadout ? 'hidden md:block' : ''}>
            <StationReadout />
          </div>
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
    </div>
  )
}

export { STATION_NAMES }
