import { useEffect, useState } from 'react'
import { useLab, type TrafficLayer, type CameraPreset } from './store'
import type { NetPathEngine } from '../sim/engine'
import type { PacketEvent } from '../sim/types'
import { STATION_NAMES } from '../sim/engine'
import { SummaryCardButton } from './SummaryCard'
import { useLiveEngineBridge } from './useLiveEngine'

// ── Left control panel ───────────────────────────────────────────────────

function Section({ title, children, sectionId }: { title: string; children: React.ReactNode; sectionId?: string }) {
  return (
    <section className="border-subtle border-b px-4 py-3" id={sectionId}>
      <h3 className="mb-2 type-micro text-nickel">
        {title.toUpperCase()}
      </h3>
      {children}
    </section>
  )
}

function CollapsibleSection({ title, children, defaultOpen = true, sectionId }: { title: string; children: React.ReactNode; defaultOpen?: boolean; sectionId?: string }) {
  const [open, setOpen] = useState(defaultOpen)
  const contentId = sectionId ? `${sectionId}-content` : undefined
  return (
    <section className="border-subtle border-b">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left font-mono text-[10px] tracking-[0.25em] text-nickel hover:text-bone"
        aria-expanded={open}
        aria-controls={contentId}
      >
        {title.toUpperCase()}
        <span className="text-bone-muted">{open ? '▾' : '▸'}</span>
      </button>
      {open && <div id={contentId} className="px-4 pb-3">{children}</div>}
    </section>
  )
}

function Slider({
  label, value, min = 0, max = 100, step = 1, suffix = '', onChange,
}: {
  label: string; value: number; min?: number; max?: number; step?: number
  suffix?: string; onChange: (v: number) => void
}) {
  return (
    <label className="mb-2 block text-xs text-[#b6c2cf]">
      <span className="flex justify-between font-mono text-[11px]">
        <span>{label}</span>
        <span className="text-[#e8e0cc]">{value}{suffix}</span>
      </span>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={`${label} slider`}
        className="mt-1 w-full accent-[#6fc7e8]"
      />
    </label>
  )
}

function Toggle({
  label, on, onClick,
}: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={on}
      className={`rounded border px-2 py-1.5 text-left font-mono text-[10px] tracking-wide transition-colors ${
        on
          ? 'border-[#6fc7e8] bg-[#6fc7e8]/10 text-[#6fc7e8]'
          : 'border-[#30363d] text-[#9d978a] hover:border-[#b08d57]'
      }`}
    >
      {label}
    </button>
  )
}

export function ControlPanel({ engine, challengeOpen, onToggleChallenge, challengeResults }: {
  engine: NetPathEngine
  challengeOpen: boolean
  onToggleChallenge: () => void
  challengeResults: Array<{ scenario: string; correct: boolean }>
}) {
  const s = useLab()
  const [followActive, setFollow] = useState(false)
  const [tracedInfo, setTracedInfo] = useState<PacketEvent | null>(null)
  const liveAttached = useLab((st) => st.liveEngineAttached)
  const liveStatus = useLab((st) => st.liveEngineStatus)
  useLiveEngineBridge()

  // Poll traced packet info at 4 Hz while tracing
  useEffect(() => {
    if (!followActive) return
    const id = setInterval(() => {
      setTracedInfo(engine.selectedPacketEvent)
    }, 250)
    return () => clearInterval(id)
  }, [followActive, engine])

  const layerLabels: Record<TrafficLayer, string> = {
    headerLayers: 'Header Layers',
    flowPaths: 'Flow Paths',
    queueDepth: 'Queue Depth',
    offloadDecisions: 'Offload Decisions',
  }
  const cameras: Array<[CameraPreset, string]> = [
    ['overview', 'Overview'],
    ['section', 'Section View'],
    ['parser', 'Parser Stage'],
    ['classifier', 'Classifier Stage'],
    ['offload', 'Offload Stage'],
    ['free', 'Free Inspection'],
  ]

  return (
    <aside
      id="control-desk"
      className="panel absolute bottom-4 left-4 top-28 z-20 w-64 overflow-y-auto"
      aria-label="Data path controls"
    >
      <Section title="Data Path" sectionId="section-data-path">
        <div className="mb-2 grid grid-cols-2 gap-2">
          <button
            onClick={s.start}
            disabled={s.pathState !== 'Idle'}
            aria-label="Start"
            className="rounded border border-success bg-success/10 py-1.5 font-mono text-[11px] text-success disabled:opacity-30"
          >
            ▶ Start
          </button>
          <button
            onClick={s.stop}
            disabled={s.pathState === 'Idle'}
            aria-label="Stop"
            className="rounded border border-[#d45f49] bg-[#d45f49]/10 py-1.5 font-mono text-[11px] text-[#d45f49] disabled:opacity-30"
          >
            ■ Stop
          </button>
        </div>
        <Slider label="Packet Rate" value={s.packetRate} suffix="%" onChange={s.setPacketRate} />
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10px] text-bone-dim">
          <span>Path State</span><span className="text-right text-air" aria-live="polite">{s.pathState}</span>
          {liveAttached ? (
            <>
              <span>Packet Rate (real)</span>
              <span className="text-right text-success">{s.livePps} pps</span>
              <span>Active Flows (real)</span>
              <span className="text-right text-bone">{s.liveFlowCount ?? s.activeFlows} flows</span>
              {s.liveDominantClass && (
                <>
                  <span>Dominant Class</span>
                  <span className="text-right text-purple">{s.liveDominantClass}</span>
                </>
              )}
              <span>Throughput (real)</span>
              <span className="text-right text-success">
                {s.throughputGbps.toFixed(3)} Gbps / {(s.throughputGbps * 125).toFixed(1)} MB/s
              </span>
            </>
          ) : (
            <>
              <span>Active Flows</span>
              <span className="text-right text-bone">{s.activeFlows} (Norm.)</span>
              <span>Throughput</span>
              <span className="text-right text-bone">Approx. {s.throughputGbps.toFixed(2)} Gbps / {(s.throughputGbps * 125).toFixed(0)} MB/s</span>
            </>
          )}
        </div>
        <div className="mt-2 border-subtle border-t pt-2">
          <button
            id="live-engine-toggle"
            onClick={() => s.setLiveEngineAttached(!liveAttached)}
            aria-pressed={liveAttached}
            aria-label="Attach to live Python engine telemetry"
            className={`w-full rounded border px-2 py-1.5 font-mono text-[10px] tracking-wide transition-colors ${
              liveStatus === 'live'
                ? 'border-success bg-success/10 text-success'
                : liveStatus === 'connecting'
                  ? 'border-warning bg-warning/10 text-warning'
                  : 'border-default text-bone-dim hover:text-bone'
            }`}
          >
            {liveStatus === 'live'
              ? '● Live Engine attached'
              : liveStatus === 'connecting'
                ? '◐ Connecting to backend…'
                : '○ Attach Live Engine'}
          </button>
          <p id="live-engine-status" className="mt-1 font-mono text-[9px] leading-relaxed text-bone-muted">
            {liveStatus === 'live'
              ? 'Readouts show real backend stats (uvicorn :8000).'
              : liveStatus === 'connecting'
                ? 'No backend on :8000 — start it: uvicorn network_lab.api.app:app'
                : 'Run the Python backend and attach for real stats.'}
          </p>
        </div>
      </Section>

      <CollapsibleSection title="Environment" defaultOpen={true}>
        <Slider
          label="Packet Size Distribution"
          value={s.sizeDist}
          suffix="% large"
          onChange={s.setSizeDist}
        />
        <select
          value={s.trafficMode}
          onChange={(e) => s.setTrafficMode(e.target.value as never)}
          aria-label="Protocol Mix"
          className="w-full rounded border border-[#30363d] bg-[#0d1117] px-2 py-1.5 text-xs text-[#e8e0cc]"
        >
          <option value="mixed">Mixed protocol profile</option>
          <option value="voiceHeavy">Voice-heavy profile</option>
          <option value="bulkHeavy">Bulk-heavy profile</option>
        </select>
        <div className="mt-2">
          <Slider label="Flow Count" value={s.flowTarget} min={1} max={64} onChange={s.setFlowTarget} />
        </div>
        <button
          onClick={s.resetToDefault}
          className="w-full rounded border border-[#30363d] py-1.5 font-mono text-[10px] text-[#9d978a] hover:border-[#b08d57]"
        >
          Reset to Default
        </button>
      </CollapsibleSection>

      <CollapsibleSection title="Model" defaultOpen={false}>
        <Slider label="Exploded View" value={Math.round(s.exploded * 100)} suffix="%" onChange={(v) => s.setExploded(v / 100)} />
        <Slider label="Pipeline Opacity" value={Math.round(s.pipelineOpacity * 100)} suffix="%" onChange={(v) => s.setPipelineOpacity(v / 100)} />
        <Toggle label="Section View" on={s.sectionView} onClick={s.toggleSectionView} />
      </CollapsibleSection>

      <Section title="Traffic Visualization">
        <div className="grid grid-cols-2 gap-2">
          {(Object.keys(layerLabels) as TrafficLayer[]).map((l) => (
            <Toggle key={l} label={layerLabels[l]} on={s.layers[l]} onClick={() => s.toggleLayer(l)} />
          ))}
        </div>
      </Section>

      <CollapsibleSection title="Follow One Packet" defaultOpen={true}>
        <Toggle
          label={followActive ? 'Tracing packet…' : 'Trace a packet'}
          on={followActive}
          onClick={() => {
            if (followActive) {
              engine.clearTraced()
              setFollow(false)
            } else if (engine.traceOne()) {
              setFollow(true)
            }
          }}
        />
        {followActive && tracedInfo && (
          <div className="mt-2 grid grid-cols-2 gap-x-2 font-mono text-[10px]" aria-live="polite">
            <span className="text-[#9d978a]">Station</span>
            <span className="text-right text-[#e8e0cc]">{tracedInfo.station} · {STATION_NAMES[tracedInfo.station]}</span>
            <span className="text-[#9d978a]">Flow Hash</span>
            <span className="text-right text-[#7ee787]">{tracedInfo.flowHash}</span>
            <span className="text-[#9d978a]">Size</span>
            <span className="text-right text-[#e8e0cc]">{tracedInfo.packetSizeB} B</span>
            <span className="text-[#9d978a]">Latency</span>
            <span className="text-right text-[#e8e0cc]">{tracedInfo.latencyMs.toFixed(1)} ms (real)</span>
          </div>
        )}
      </CollapsibleSection>

      <Section title="Camera">
        <div className="grid grid-cols-2 gap-2">
          {cameras.map(([p, label]) => (
            <Toggle key={p} label={label} on={s.cameraPreset === p} onClick={() => s.setCameraPreset(p)} />
          ))}
        </div>
      </Section>

      <Section title="Learning">
        <Toggle label={`Sound ${s.soundEnabled ? 'On' : 'Off'}`} on={s.soundEnabled} onClick={s.toggleSound} />
        <div className="mt-2">
          <button
            onClick={onToggleChallenge}
            aria-expanded={challengeOpen}
            aria-controls="fault-challenge"
            className={`w-full rounded border px-2 py-1.5 font-mono text-[10px] tracking-wide transition-colors ${
              challengeOpen
                ? 'border-[#f2c45f] bg-[#f2c45f]/10 text-[#f2c45f]'
                : 'border-[#30363d] text-[#9d978a] hover:border-[#b08d57]'
            }`}
          >
            Fault Challenge
          </button>
        </div>
        <SummaryCardButton results={challengeResults} />
      </Section>
    </aside>
  )
}
