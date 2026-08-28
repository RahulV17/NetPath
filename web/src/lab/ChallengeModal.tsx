/**
 * ChallengeModal — fault-diagnosis challenges (spec §4, non-blocking panel).
 *
 * Flow: pick scenario → "Select the affected stage" →
 *       "Choose the best causal explanation" → feedback.
 *
 * Each scenario mutates live engine state via the verified injection
 * points; closing or answering correctly restores normal behavior.
 */
import { useEffect, useRef, useState } from 'react'
import { STATIONS } from './store'
import type { NetPathEngine } from '../sim/engine'

export type ChallengeId =
  | 'aclMisconfig'
  | 'classifierDrift'
  | 'offloadOverflow'
  | 'qosStarvation'

interface Scenario {
  id: ChallengeId
  title: string
  correctStage: number
  inject: (e: NetPathEngine) => void
  clear: (e: NetPathEngine) => void
  options: string[]
  correctOption: number
}

const SCENARIOS: Scenario[] = [
  {
    id: 'aclMisconfig',
    title: 'ACL Misconfiguration',
    correctStage: 2,
    inject: (e) => e.injectAclMisconfiguration(),
    clear: (e) => e.restoreDefaultAcls(),
    options: [
      'The parser is dropping packets because headers are malformed.',
      'An overly broad firewall rule is matching every flow at Station 2 and discarding it.',
      'The hardware offload cache overflowed, so packets fall to the CPU path.',
      'The QoS token bucket ran out, so bulk traffic is being policed away.',
    ],
    correctOption: 1,
  },
  {
    id: 'classifierDrift',
    title: 'Classifier Drift',
    correctStage: 3,
    inject: (e) => e.injectClassifierDrift(),
    clear: (e) => e.clearDrift(),
    options: [
      'A bridge loop is flooding duplicate frames across every port.',
      'The MAC learning table aged out all entries at once.',
      'Correct — the classifier lost its training signal and began mislabeling bulk traffic as voice, causing queue starvation.',
      'TTL expired on every packet because the routing table was empty.',
    ],
    // Spec copy places the correct explanation at index 2 in this scenario
    correctOption: 2,
  },
  {
    id: 'offloadOverflow',
    title: 'Offload Cache Overflow',
    correctStage: 4,
    inject: (e) => e.injectOffloadOverflow(),
    clear: (e) => {
      e.hwCacheClear()
    },
    options: [
      'The HW flow cache filled up, so every new flow falls back to the CPU path.',
      'The ML classifier began labeling all flows as best effort.',
      'The parser stopped decoding VLAN tags, so trunks rejected frames.',
      'Voice traffic exceeded its DSCP marking and was re-queued as bulk.',
    ],
    correctOption: 0,
  },
  {
    id: 'qosStarvation',
    title: 'QoS Starvation',
    correctStage: 6,
    inject: (e) => e.injectQosStarvation(),
    clear: (e) => e.clearQosStarvation(),
    options: [
      'The ACL is discarding packets with a specific destination port.',
      'The fast-path arc collapsed, forcing packets through Station 5 twice.',
      'The flow hash function started colliding, merging distinct flows.',
      'A shrunken token bucket can no longer sustain bulk flows, so policing starves them while small voice packets still pass.',
    ],
    correctOption: 3,
  },
]

type Phase = 'pick' | 'stage' | 'explain' | 'feedback'

interface Props {
  engine: NetPathEngine
  open: boolean
  onClose: () => void
  /**
   * Fires ONCE per scenario completion — on the correct answer, or on
   * giving up (close) with `false`. Wrong attempts do not fire.
   */
  onResult?: (scenario: string, correct: boolean) => void
}

export function ChallengeModal({ engine, open, onClose, onResult }: Props) {
  const [phase, setPhase] = useState<Phase>('pick')
  const [scenario, setScenario] = useState<Scenario | null>(null)
  const [pickedStage, setPickedStage] = useState<number | null>(null)
  const [answered, setAnswered] = useState<number | null>(null)
  const [solvedCurrent, setSolvedCurrent] = useState(false)
  const reportedRef = useRef(false)

  const cleanupScenario = () => {
    if (scenario) {
      scenario.clear(engine)
      // Report once per scenario attempt-cycle
      if (!reportedRef.current) {
        reportedRef.current = true
        onResult?.(scenario.title, solvedCurrent)
      }
    }
    setScenario(null)
    setPickedStage(null)
    setAnswered(null)
    setSolvedCurrent(false)
  }

  // Clean up engine state whenever the panel closes
  useEffect(() => {
    if (!open) cleanupScenario()
    setPhase('pick')
    setAnswered(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open) return null

  const close = () => {
    cleanupScenario()
    onClose()
  }

  const start = (s: Scenario) => {
    s.inject(engine)
    setScenario(s)
    setPhase('stage')
    reportedRef.current = false
  }

  const stageStreak = pickedStage === scenario?.correctStage

  return (
    <div
      id="fault-challenge"
      className="absolute bottom-4 left-1/2 z-30 w-[min(560px,calc(100vw-24px))] -translate-x-1/2 rounded-lg border border-nickel/60 bg-panel p-4 backdrop-blur"
      role="dialog"
      aria-label="Fault Challenge"
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-display text-sm text-bone">Fault Challenge</h3>
        <button
          onClick={close}
          aria-label="Close fault challenge"
          className="rounded border border-[#30363d] px-2 py-1 font-mono text-[10px] text-[#9d978a] hover:border-[#b08d57]"
        >
          CLOSE
        </button>
      </div>

      {phase === 'pick' && (
        <>
          <p className="mb-2 text-xs text-[#b6c2cf]">
            Pick a failure mode. Traffic will start misbehaving — diagnose it.
          </p>
          <div className="grid grid-cols-2 gap-2">
            {SCENARIOS.map((s) => (
              <button
                key={s.id}
                onClick={() => start(s)}
                className="rounded border border-[#30363d] px-3 py-2 text-left font-mono text-[11px] text-[#e8e0cc] hover:border-[#f2c45f]"
              >
                {s.title}
              </button>
            ))}
          </div>
        </>
      )}

      {phase === 'stage' && scenario && (
        <>
          <p className="mb-2 text-xs text-[#b6c2cf]">{scenario.title} — Select the affected stage.</p>
          <div className="mb-1 flex gap-1.5 overflow-x-auto" role="group" aria-label="Select the affected stage">
            {STATIONS.map((st) => (
              <button
                key={st.id}
                onClick={() => setPickedStage(st.id)}
                aria-pressed={pickedStage === st.id}
                className={`shrink-0 rounded border px-2.5 py-1.5 font-mono text-[10px] ${
                  pickedStage === st.id
                    ? 'border-[#f2c45f] bg-[#f2c45f]/10 text-[#f2c45f]'
                    : 'border-[#30363d] text-[#9d978a]'
                }`}
              >
                {st.id}
              </button>
            ))}
          </div>
          <div className="mt-1 mb-3 min-h-4 font-mono text-[10px] text-[#9d978a]">
            {pickedStage !== null ? `Station ${pickedStage} · ${STATIONS[pickedStage].name}` : '\u00A0'}
          </div>
          <button
            disabled={pickedStage === null}
            onClick={() => setPhase('explain')}
            className="w-full rounded border border-[#6fc7e8] py-1.5 font-mono text-[11px] text-[#6fc7e8] disabled:opacity-30"
          >
            CONTINUE
          </button>
        </>
      )}

      {phase === 'explain' && scenario && (
        <>
          <p className="mb-2 text-xs text-[#b6c2cf]">Choose the best causal explanation.</p>
          <div className="space-y-1.5" role="radiogroup" aria-label="Causal explanations">
            {scenario.options.map((opt, i) => (
              <button
                key={i}
                onClick={() => {
                  setAnswered(i)
                  setPhase('feedback')
                  if (i === scenario.correctOption) {
                    setSolvedCurrent(true)
                  }
                }}
                className={`block w-full rounded border px-3 py-2 text-left text-[11px] leading-snug transition-colors ${
                  answered !== null && i === scenario.correctOption
                    ? 'border-[#39d353] bg-[#39d353]/10 text-[#7ee787]'
                    : answered !== null && i === answered && i !== scenario.correctOption
                      ? 'border-[#d45f49] bg-[#d45f49]/10 text-[#ffa198]'
                      : answered !== null
                        ? 'border-[#30363d] text-[#9d978a]'
                        : 'border-[#30363d] text-[#b6c2cf] hover:border-[#b08d57]'
                }`}
              >
                {opt}
              </button>
            ))}
          </div>
        </>
      )}

      {phase === 'feedback' && scenario && (
        <div aria-live="polite">
          {answered === scenario.correctOption ? (
            <div className="space-y-3">
              <p className="text-xs leading-relaxed text-[#7ee787]">
                Correct — {scenario.id === 'classifierDrift'
                  ? 'the classifier lost its training signal and began mislabeling bulk traffic as voice, causing queue starvation.'
                  : `${scenario.title}: your diagnosis matches the observed behavior at Station ${scenario.correctStage}.`}
              </p>
              {!stageStreak && (
                <p className="text-[11px] text-[#f2c45f]">
                  Tip — the affected station was Station {scenario.correctStage} ·{' '}
                  {STATIONS[scenario.correctStage].name}. Try again and follow the packet path.
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-[#ffa198]">
                Not quite. Try again and follow the packet path.
              </p>
              <button
                onClick={() => {
                  setPhase('stage')
                  setPickedStage(null)
                  setAnswered(null)
                }}
                className="rounded border border-[#6fc7e8] px-3 py-1.5 font-mono text-[10px] text-[#6fc7e8]"
              >
                RETRY
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export { SCENARIOS }

