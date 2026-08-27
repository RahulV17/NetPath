/**
 * SummaryCard — renders a learning-summary card to canvas and downloads
 * it as PNG (spec §9). English-only content per spec.
 *
 * Card contents: NetPath title, chapters completed, active visualization
 * layer, fault-challenge results, and the spec's closing insight line.
 */

export interface ChallengeResult {
  scenario: string
  correct: boolean
}

export function drawSummaryCard(
  canvas: HTMLCanvasElement,
  opts: {
    chaptersVisited: number[]
    activeLayer: string
    results: ChallengeResult[]
    throughputGbps: number
  },
): void {
  const W = 900
  const H = 620
  canvas.width = W
  canvas.height = H
  const g = canvas.getContext('2d')
  if (!g) return

  // Background
  g.fillStyle = '#05070a'
  g.fillRect(0, 0, W, H)

  // Border — brass museum plaque feel
  g.strokeStyle = '#b08d57'
  g.lineWidth = 2
  g.strokeRect(24, 24, W - 48, H - 48)
  g.strokeStyle = 'rgba(176, 141, 87, 0.35)'
  g.strokeRect(32, 32, W - 64, H - 64)

  // Header
  g.fillStyle = '#b08d57'
  g.font = '600 13px "JetBrains Mono", monospace'
  g.textAlign = 'center'
  g.fillText('T H E   P A C K E T   E N G I N E   ·   N o . 0 1', W / 2, 78)

  g.fillStyle = '#e8e0cc'
  g.font = '600 34px "Source Serif 4", Georgia, serif'
  g.fillText('NetPath · Network Data Path Lab', W / 2, 122)

  g.fillStyle = '#9d978a'
  g.font = '400 14px Inter, sans-serif'
  g.fillText('Learning Summary', W / 2, 150)

  // Chapters completed
  g.textAlign = 'left'
  const x0 = 80
  let y = 200

  g.fillStyle = '#6fc7e8'
  g.font = '500 13px "JetBrains Mono", monospace'
  g.fillText('COMPLETED CHAPTERS', x0, y)
  y += 12
  const chapterTitles = [
    'Why packets must be parsed before forwarding',
    'How the ACL decides which packets to drop',
    'What the ML classifier learns from flow features',
    'When hardware takes over from the CPU',
    'How QoS guarantees bandwidth for voice traffic',
    'Follow a packet through the complete data path',
  ]
  g.font = '400 15px Inter, sans-serif'
  for (const idx of [...opts.chaptersVisited].sort((a, b) => a - b)) {
    g.fillStyle = '#39d353'
    g.fillText('✓', x0 + 4, y + 16)
    g.fillStyle = '#e8e0cc'
    g.fillText(`Chapter ${idx + 1} — ${chapterTitles[idx] ?? ''}`, x0 + 26, y + 16)
    y += 26
  }
  if (opts.chaptersVisited.length === 0) {
    g.fillStyle = '#484f58'
    g.fillText('No chapters visited yet.', x0 + 4, y + 16)
    y += 26
  }

  // Active visualization layer
  y += 10
  g.fillStyle = '#6fc7e8'
  g.font = '500 13px "JetBrains Mono", monospace'
  g.fillText('ACTIVE VISUALIZATION LAYER', x0, y)
  g.fillStyle = '#e8e0cc'
  g.font = '400 15px Inter, sans-serif'
  g.fillText(opts.activeLayer, x0, y + 22)
  y += 52

  // Fault challenge results
  g.fillStyle = '#6fc7e8'
  g.font = '500 13px "JetBrains Mono", monospace'
  g.fillText('FAULT CHALLENGE RESULTS', x0, y)
  y += 8
  g.font = '400 15px Inter, sans-serif'
  for (const r of opts.results.slice(-4)) {
    g.fillStyle = r.correct ? '#39d353' : '#d45f49'
    g.fillText(r.correct ? 'PASS' : 'MISS', x0 + 4, y + 18)
    g.fillStyle = '#b6c2cf'
    g.fillText(r.scenario, x0 + 64, y + 18)
    y += 24
  }
  if (opts.results.length === 0) {
    g.fillStyle = '#484f58'
    g.fillText('No challenges attempted.', x0 + 4, y + 18)
    y += 24
  }

  // Throughput footnote (normalized)
  g.fillStyle = '#9d978a'
  g.font = '400 12px Inter, sans-serif'
  g.fillText(
    `Peak observed throughput: Approx. ${opts.throughputGbps.toFixed(2)} Gbps (normalized)`,
    x0,
    H - 96,
  )

  // Closing insight — spec §4 line
  g.fillStyle = '#f2c45f'
  g.font = 'italic 500 16px "Source Serif 4", Georgia, serif'
  g.textAlign = 'center'
  g.fillText(
    'I finally understand: the classifier learns behavior, hardware accelerates',
    W / 2,
    H - 62,
  )
  g.fillText(
    'the fast path, and QoS ensures every flow gets its fair share.',
    W / 2,
    H - 40,
  )
}
