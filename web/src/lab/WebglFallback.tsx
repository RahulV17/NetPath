/**
 * Fallback SVG cutaway shown when WebGL is unavailable (spec §10).
 * English-only, static, no canvas.
 */
export function WebglFallback() {
  return (
    <div
      className="absolute inset-0 flex flex-col items-center justify-center gap-6 px-8"
      role="alert"
    >
      <svg viewBox="0 0 900 220" className="w-full max-w-3xl" aria-label="Static data path diagram">
        {/* conduit */}
        <line x1="40" y1="110" x2="860" y2="110" stroke="#30363d" strokeWidth="26" />
        {[
          ['0 Ingress', '#8b949e'],
          ['1 Parser', '#58a6ff'],
          ['2 ACL', '#f85149'],
          ['3 ML', '#bc8cff'],
          ['4 Offload', '#39d353'],
          ['5 L2/L3', '#58a6ff'],
          ['6 QoS', '#ffa657'],
          ['7 Egress', '#e6edf3'],
        ].map(([label, color], i) => {
          const x = 70 + i * 105
          return (
            <g key={i}>
              <rect x={x - 30} y="82" width="60" height="56" rx="8" fill="#161b22" stroke={color} strokeWidth="1.5" />
              <circle cx={x} cy="102" r="9" fill={color} />
              <text x={x} y="160" fill="#9d978a" fontSize="11" textAnchor="middle">{label}</text>
            </g>
          )
        })}
      </svg>
      <p className="max-w-md text-center text-sm text-[#b6c2cf]">
        The Network Data Path Lab could not start. Enable hardware acceleration
        or use a modern browser with WebGL support.
      </p>
    </div>
  )
}
