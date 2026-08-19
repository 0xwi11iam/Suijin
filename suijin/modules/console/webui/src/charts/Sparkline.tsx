/** Minimal sparkline for stat cards. */
export default function Sparkline({ data, color = "var(--accent)", w = 120, h = 32 }: {
  data: number[]; color?: string; w?: number; h?: number
}) {
  if (data.length < 2) data = [0, 0]
  const max = Math.max(...data, 1)
  const min = Math.min(...data, 0)
  const span = max - min || 1
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / span) * (h - 4) - 2}`)
  return (
    <svg width={w} height={h} aria-hidden>
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" />
      <circle cx={w} cy={h - ((data[data.length - 1] - min) / span) * (h - 4) - 2} r="2.5" fill={color} />
    </svg>
  )
}
