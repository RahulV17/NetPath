/**
 * SummaryCard — button + offscreen canvas → PNG download.
 */
import { useRef, useState } from 'react'
import { useLab } from './store'
import { drawSummaryCard } from './summaryContent'
import { sfx } from './audio'

export function SummaryCardButton({ results }: { results: Array<{ scenario: string; correct: boolean }> }) {
  const [downloaded, setDownloaded] = useState(false)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  const generate = () => {
    const s = useLab.getState()
    const canvas = canvasRef.current ?? document.createElement('canvas')
    canvasRef.current = canvas

    // Pick the currently-enabled layer name for the card
    const enabledLayers = Object.entries(s.layers)
      .filter(([, on]) => on)
      .map(([k]) => k)
    const activeLayer =
      enabledLayers.length > 0 ? `Traffic visualization: ${enabledLayers.join(', ')}` : 'All layers hidden'

    drawSummaryCard(canvas, {
      chaptersVisited: s.chaptersVisited,
      activeLayer,
      results,
      throughputGbps: s.throughputGbps,
    })

    canvas.toBlob((blob) => {
      if (!blob) return
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'netpath-learning-summary.png'
      a.click()
      // Revoke on next tick — immediate revoke can abort the download
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      setDownloaded(true)
      setTimeout(() => setDownloaded(false), 2500)
    }, 'image/png')
  }

  return (
    <div className="mt-2">
      <button
        onClick={() => {
          sfx.complete()
          generate()
        }}
        aria-label="Download learning summary card"
        className="w-full rounded border border-[#b08d57]/70 bg-[#b08d57]/10 px-2 py-1.5 font-mono text-[10px] tracking-wide text-[#f2c45f] hover:bg-[#b08d57]/20"
      >
        {downloaded ? 'Saved ✓' : 'Download Summary Card'}
      </button>
    </div>
  )
}
