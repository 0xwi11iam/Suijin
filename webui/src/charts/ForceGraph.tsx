import { useEffect, useRef, useState } from "react"

/**
 * Force-directed knowledge-graph — simple physics in rAF, no deps.
 * Nodes: attacker(red) attack(amber) defense(blue) endpoint(cyan) other(grey).
 */
export interface GNode { id: string; type: string; label: string; flags?: number }
export interface GEdge { from: string; to: string }

interface Sim extends GNode { x: number; y: number; vx: number; vy: number }

const COLORS: Record<string, string> = {
  attacker: "#ff3366",
  attack: "#ffa500",
  defense: "#3366ff",
  deception: "#9d4edd",
  endpoint: "#00c9ff",
  intelligence: "#8899bb",
}
const TYPE_LABELS: Record<string, string> = {
  attacker: "attacker", attack: "attack", defense: "defense",
  deception: "deception", endpoint: "endpoint", intelligence: "intel",
}

export default function ForceGraph({ nodes, edges, height = 460, onPick }: {
  nodes: GNode[]; edges: GEdge[]; height?: number; onPick?: (n: GNode) => void
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const simsRef = useRef<Sim[]>([])
  const [hover, setHover] = useState<{ x: number; y: number; label: string } | null>(null)
  const pickedRef = useRef<string | null>(null)

  // reconcile sim nodes when props change (preserve positions)
  const prevIds = new Set(simsRef.current.map((s) => s.id))
  const next: Sim[] = nodes.map((n) => {
    const old = simsRef.current.find((s) => s.id === n.id)
    return old
      ? { ...old, ...n }
      : { ...n, x: (Math.random() - 0.5) * 200 + 300, y: (Math.random() - 0.5) * 200 + 200, vx: 0, vy: 0 }
  })
  simsRef.current = next
  const ids = new Set(next.map((n) => n.id))
  const edgesFiltered = edges.filter((e) => ids.has(e.from) && ids.has(e.to))
  prevIds.clear()

  useEffect(() => {
    const cv = ref.current!
    const ctx = cv.getContext("2d")!
    let stop = false
    let iter = 0

    const tick = () => {
      if (stop) return
      const w = cv.clientWidth
      const h = height
      if (cv.width !== w * devicePixelRatio) {
        cv.width = w * devicePixelRatio
        cv.height = h * devicePixelRatio
      }
      ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0)
      ctx.clearRect(0, 0, w, h)
      const sims = simsRef.current
      if (sims.length === 0) {
        raf = requestAnimationFrame(tick)
        return
      }

      // physics — run harder for the first 300 frames, then relax
      const k = iter < 300 ? 1 : 0.15
      iter++
      for (let i = 0; i < sims.length; i++) {
        for (let j = i + 1; j < sims.length; j++) {
          const a = sims[i], b = sims[j]
          let dx = b.x - a.x, dy = b.y - a.y
          let d2 = dx * dx + dy * dy
          if (d2 < 1) { d2 = 1; dx = Math.random(); dy = Math.random() }
          const f = (1400 / d2) * k
          const d = Math.sqrt(d2)
          const fx = (dx / d) * f, fy = (dy / d) * f
          a.vx -= fx; a.vy -= fy
          b.vx += fx; b.vy += fy
        }
      }
      for (const e of edgesFiltered) {
        const a = sims.find((s) => s.id === e.from)
        const b = sims.find((s) => s.id === e.to)
        if (!a || !b) continue
        const dx = b.x - a.x, dy = b.y - a.y
        const d = Math.sqrt(dx * dx + dy * dy) || 1
        const f = ((d - 120) * 0.004) * k
        a.vx += (dx / d) * f * 10; a.vy += (dy / d) * f * 10
        b.vx -= (dx / d) * f * 10; b.vy -= (dy / d) * f * 10
      }
      for (const s of sims) {
        // centering + damping
        s.vx += (w / 2 - s.x) * 0.0009
        s.vy += (height / 2 - s.y) * 0.0009
        s.vx *= 0.82; s.vy *= 0.82
        s.x += Math.max(-6, Math.min(6, s.vx))
        s.y += Math.max(-6, Math.min(6, s.vy))
        s.x = Math.max(24, Math.min(w - 24, s.x))
        s.y = Math.max(24, Math.min(height - 24, s.y))
      }

      // edges
      for (const e of edgesFiltered) {
        const a = sims.find((s) => s.id === e.from)
        const b = sims.find((s) => s.id === e.to)
        if (!a || !b) continue
        ctx.strokeStyle = "rgba(136,153,187,0.18)"
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(a.x, a.y)
        ctx.lineTo(b.x, b.y)
        ctx.stroke()
      }

      // nodes
      let hoverTarget: { x: number; y: number; label: string } | null = null
      for (const s of sims) {
        const r = Math.min(14, 4 + (s.flags ?? 0))
        const col = COLORS[s.type] ?? "#8899bb"
        const isPicked = pickedRef.current === s.id
        ctx.fillStyle = col
        ctx.beginPath()
        ctx.arc(s.x, s.y, r, 0, Math.PI * 2)
        ctx.fill()
        if (isPicked) {
          ctx.strokeStyle = "rgba(0,255,136,0.8)"
          ctx.lineWidth = 2
          ctx.beginPath()
          ctx.arc(s.x, s.y, r + 5, 0, Math.PI * 2)
          ctx.stroke()
        }
        if (hover && Math.abs(hover.x - s.x) < 14 && Math.abs(hover.y - s.y) < 14) {
          hoverTarget = { x: s.x, y: s.y - r - 10, label: `${s.label} · ${TYPE_LABELS[s.type] ?? s.type}` }
        }
        // label for larger nodes
        if (r >= 7 || isPicked) {
          ctx.fillStyle = "rgba(255,255,255,0.75)"
          ctx.font = "10px 'JetBrains Mono', monospace"
          ctx.textAlign = "center"
          ctx.fillText(s.label, s.x, s.y + r + 12)
        }
      }
      if (hoverTarget) setHover(hoverTarget)
      else if (hover) setHover(null)

      raf = requestAnimationFrame(tick)
    }
    let raf = requestAnimationFrame(tick)
    return () => { stop = true; cancelAnimationFrame(raf) }
  }, [height, nodes, edges, hover])

  return (
    <div style={{ position: "relative" }}>
      <canvas
        ref={ref}
        style={{ width: "100%", height, cursor: "pointer" }}
        onMouseMove={(e) => {
          const rect = (e.target as HTMLCanvasElement).getBoundingClientRect()
          setHover({ x: e.clientX - rect.left, y: e.clientY - rect.top, label: "" })
        }}
        onClick={(e) => {
          const rect = (e.target as HTMLCanvasElement).getBoundingClientRect()
          const mx = e.clientX - rect.left
          const my = e.clientY - rect.top
          const hit = simsRef.current.find((s) => Math.hypot(s.x - mx, s.y - my) < 16)
          pickedRef.current = hit?.id ?? null
          if (hit && onPick) onPick(hit)
        }}
        aria-label="knowledge graph"
      />
      {hover?.label && (
        <div
          className="graph-tip mono"
          style={{ position: "absolute", left: hover.x + 12, top: hover.y - 6 }}
        >
          {hover.label}
        </div>
      )}
      <div className="graph-legend small">
        {Object.entries(COLORS).map(([t, c]) => (
          <span key={t} style={{ color: c }}>● {TYPE_LABELS[t] ?? t}</span>
        ))}
      </div>
    </div>
  )
}
