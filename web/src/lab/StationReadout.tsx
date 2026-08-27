// ── Right station readout panel ──────────────────────────────────────────

import { STATIONS, useLab } from './store'

export function StationReadout() {
  const selected = useLab((s) => s.selectedStation)
  const selectStation = useLab((s) => s.selectStation)
  const r = useLab((s) => s.readouts[selected])

  return (
    <aside
      id="engine-readout"
      className="absolute right-4 top-28 z-20 w-72 rounded-lg border border-[#30363d] bg-[rgba(7,9,12,0.84)] backdrop-blur"
      aria-label="Station data"
    >
      {/* Station tabs */}
      <div className="flex overflow-x-auto border-b border-[#1c2530]" role="tablist" aria-label="Stations">
        {STATIONS.map((st) => (
          <button
            key={st.id}
            role="tab"
            aria-selected={selected === st.id}
            onClick={() => selectStation(st.id)}
            className={`shrink-0 px-3 py-2 font-mono text-[10px] tracking-wide transition-colors ${
              selected === st.id
                ? 'border-b-2 border-[#f2c45f] text-[#e8e0cc]'
                : 'text-[#9d978a] hover:text-[#b6c2cf]'
            }`}
          >
            {st.id}
          </button>
        ))}
      </div>

      <div className="px-4 py-3">
        <div className="mb-1 flex items-baseline justify-between">
          <h3 className="font-serif text-sm text-[#e8e0cc]">
            Station {selected} · {STATIONS[selected].name}
          </h3>
          <span className="font-mono text-[9px] text-[#9d978a]">NORMALIZED</span>
        </div>

        {/* Data path diagram strip */}
        <svg viewBox="0 0 280 26" className="mb-3" aria-hidden="true">
          <line x1="8" y1="13" x2="272" y2="13" stroke="#30363d" strokeWidth="6" />
          {STATIONS.map((st) => (
            <g key={st.id}>
              <circle
                cx={16 + st.id * 34} cy={13} r={5.5}
                fill={st.id === selected ? '#f2c45f' : '#1c2530'}
                stroke={st.id === selected ? '#f2c45f' : '#58a6ff'}
                strokeWidth="1.2"
              />
            </g>
          ))}
        </svg>

        <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono text-[10.5px]">
          <dt className="text-[#9d978a]">Packet Size</dt>
          <dd className="text-right text-[#e8e0cc]">{r?.packetSize ?? '—'}</dd>

          <dt className="text-[#9d978a]">Header Type</dt>
          <dd className="truncate text-right text-[#e8e0cc]" title={r?.headerType}>{r?.headerType ?? '—'}</dd>

          <dt className="text-[#9d978a]">Flow Hash</dt>
          <dd className="text-right text-[#7ee787]">{r?.flowHash ?? '—'}</dd>

          <dt className="text-[#9d978a]">Offload Target</dt>
          <dd className="text-right text-[#39d353]">{r?.offloadTarget ?? '—'}</dd>

          <dt className="text-[#9d978a]">Traffic Class</dt>
          <dd className="text-right text-[#bc8cff]">{r?.trafficClass ?? '—'}</dd>

          <dt className="text-[#9d978a]">Queue Depth</dt>
          <dd className="text-right text-[#ffa657]">{r?.queueDepth ?? 0}</dd>

          <dt className="text-[#9d978a]">Latency</dt>
          <dd className="text-right text-[#e8e0cc]">{r ? `${r.latencyMs.toFixed(1)} ms (est.)` : '—'}</dd>
        </dl>
      </div>
    </aside>
  )
}

// ── Disclaimer ───────────────────────────────────────────────────────────

export function Disclaimer() {
  return (
    <p className="pointer-events-none absolute bottom-4 left-1/2 z-10 w-max max-w-md -translate-x-1/2 rounded border border-[#30363d] bg-[rgba(7,9,12,0.84)] px-4 py-2 text-center font-mono text-[10px] leading-relaxed text-[#9d978a]">
      Educational model: trends and causal relationships are representative;
      values are not intended for production network design, certification,
      administration, or security calculations.
    </p>
  )
}
