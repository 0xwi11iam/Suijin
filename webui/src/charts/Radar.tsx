/** Attack-pattern radar chart — hand-rolled SVG, no deps. */
interface Props {
  data: { label: string; value: number }[]  // value 0..1
  size?: number
}

export default function Radar({ data, size = 260 }: Props) {
  const cx = size / 2
  const cy = size / 2
  const r = size / 2 - 42
  const n = Math.max(3, data.length)
  const pt = (i: number, v: number) => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2
    return [cx + Math.cos(a) * r * v, cy + Math.sin(a) * r * v]
  }
  const poly = data.map((d, i) => pt(i, Math.max(0.04, d.value)).join(",")).join(" ")
  return (
    <svg width="100%" viewBox={`0 0 ${size} ${size}`} role="img" aria-label="attack pattern radar">
      {[0.25, 0.5, 0.75, 1].map((g) => (
        <polygon
          key={g}
          points={data.map((_, i) => pt(i, g).join(",")).join(" ")}
          fill="none"
          stroke="rgba(255,255,255,0.06)"
        />
      ))}
      {data.map((_, i) => (
        <line key={i} x1={cx} y1={cy} x2={pt(i, 1)[0]} y2={pt(i, 1)[1]} stroke="rgba(255,255,255,0.05)" />
      ))}
      <polygon points={poly} fill="rgba(0,255,136,0.12)" stroke="var(--accent)" strokeWidth="1.5" />
      {data.map((d, i) => {
        const [x, y] = pt(i, 1.16)
        return (
          <text key={d.label} x={x} y={y} textAnchor="middle" fontSize="9" fill="var(--text-secondary)">
            {d.label}
          </text>
        )
      })}
      {data.map((d, i) => {
        const [x, y] = pt(i, Math.max(0.04, d.value))
        return <circle key={d.label + "p"} cx={x} cy={y} r="3" fill="var(--accent)" className="pulse" />
      })}
    </svg>
  )
}
