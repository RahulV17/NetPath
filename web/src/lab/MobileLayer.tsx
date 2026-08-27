/**
 * MobileLayer — spec §7 behaviors, active only at ≤768px (via useIsMobile).
 *
 * - Bottom control drawer (collapsed default, 54dvh cap, safe-area)
 * - Collapsible station-data layer (38dvh cap, scrollable station tabs)
 * - Mutual exclusion between the two
 * - Dismissible desktop-recommendation notice (sessionStorage)
 */
import { useEffect, useState } from 'react'
import { useLab } from './store'

export function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches,
  )
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)')
    const onChange = () => setMobile(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return mobile
}

/** Hide desktop-absolute panels on mobile; they render inside drawers instead. */
export function useDesktopPanelVisibility() {
  const isMobile = useIsMobile()
  const controlOpen = useLab((s) => s.isControlPanelOpen)
  const readoutOpen = useLab((s) => s.isReadoutOpen)
  return {
    hideControl: isMobile || !controlOpen,
    hideReadout: isMobile || !readoutOpen,
    isMobile,
  }
}

// ── Desktop recommendation notice ────────────────────────────────────────

export function DesktopHint() {
  const dismiss = useLab((s) => s.dismissDesktopHint)
  const [show, setShow] = useState(false)

  useEffect(() => {
    let dismissed = false
    try {
      dismissed = sessionStorage.getItem('netpath-hint-dismissed') === '1'
    } catch { /* private mode */ }
    setShow(!dismissed)
  }, [])

  if (!show) return null

  return (
    <div
      className="pointer-events-auto absolute inset-x-3 top-[104px] z-30 flex items-start gap-2 rounded border border-[#6fc7e8]/40 bg-[rgba(7,9,12,0.88)] px-3 py-2 backdrop-blur md:hidden"
      role="status"
    >
      <p className="flex-1 text-[11px] leading-relaxed text-[#e8e0cc]">
        For the most complete 3D interaction, open this experience on a
        desktop or laptop.
      </p>
      <button
        onClick={() => {
          dismiss()
          setShow(false)
        }}
        aria-label="Dismiss desktop recommendation"
        className="-mr-1 -mt-1 shrink-0 p-1.5 font-mono text-xs text-[#9d978a]"
      >
        ✕
      </button>
    </div>
  )
}

// ── Mobile bottom bars ───────────────────────────────────────────────────

function MobileBars() {
  const openControlPanel = useLab((s) => s.openControlPanel)
  const openReadout = useLab((s) => s.openReadout)
  const pathState = useLab((s) => s.pathState)   // reactive — getState() froze the chip
  const rate = useLab((s) => s.packetRate)
  const isMobile = useIsMobile()

  return (
    <div
      className="absolute inset-x-0 bottom-0 z-40 grid grid-cols-2 gap-2 px-3 pb-[calc(10px+env(safe-area-inset-bottom))] md:hidden"
      style={{ minHeight: 48 }}
    >
      <button
        onClick={() => openControlPanel(isMobile)}
        aria-expanded={false}
        aria-controls="control-desk"
        className="rounded-lg border border-[#30363d] bg-[rgba(7,9,12,0.9)] py-3 font-mono text-[11px] text-[#e8e0cc] backdrop-blur"
      >
        Open Data Path Controls
        <span className="ml-1 text-[#6fc7e8]">{pathState} · Rate {rate}%</span>
      </button>
      <button
        onClick={() => openReadout(isMobile)}
        aria-expanded={false}
        aria-controls="engine-readout"
        className="rounded-lg border border-[#30363d] bg-[rgba(7,9,12,0.9)] py-3 font-mono text-[11px] text-[#e8e0cc] backdrop-blur"
      >
        Station Data · Ingress
      </button>
    </div>
  )
}

// ── Drawer close buttons rendered inside the panels on mobile ───────────

export function DrawerClose({ id }: { id: 'control-desk' | 'engine-readout' }) {
  const isMobile = useIsMobile()
  if (!isMobile) return null

  return (
    <button
      onClick={() => useLab.setState({ isControlPanelOpen: false, isReadoutOpen: false })}
      aria-label={id === 'control-desk' ? 'Close Controls' : 'Close Station Data'}
      className="mb-2 w-full rounded border border-[#30363d] py-1.5 font-mono text-[10px] text-[#9d978a]"
    >
      Close {id === 'control-desk' ? 'Controls' : 'Station Data'}
    </button>
  )
}

export { MobileBars }
