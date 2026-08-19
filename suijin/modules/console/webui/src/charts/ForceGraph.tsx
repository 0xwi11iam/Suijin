import { useEffect, useRef } from "react"

/**
 * Force-directed knowledge-graph — simple physics in rAF, no deps.
 * All interaction state lives in refs; the rAF loop never calls setState
 * (the first version restarted physics on every mousemove — janky).
 */
export interface GNode { id: string; type: string; label: string; flags?: number }
export interface GEdge { from: string; to: string }

interface Sim extends GNode { x: number; y: number; vx: number; vy: number; deg: number; r?: number }

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
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const simsRef = useRef<Sim[]>([])
  const hoverRef = useRef<{ x: number; y: number } | null>(null)
  const pickedRef = useRef<string | null>(null)
  const propsRef = useRef({ nodes, edges })
  propsRef.current = { nodes, edges }

  // reconcile sim nodes when props change (preserve positions)
  const prev = simsRef.current
  simsRef.current = nodes.map((n) => {
    const old = prev.find((s) => s.id === n.id)
    return old
      ? { ...old, ...n }
      : { ...n, x: 260 + (Math.random() - 0.5) * 220, y: height / 2 + (Math.random() - 0.5) * 220, vx: 0, vy: 0, deg: 0 }
  })
  const deg: Record<string, number> = {}
  const ids = new Set(simsRef.current.map((s) => s.id))
  for (const e of edges) {
    if (ids.has(e.from)) deg[e.from] = (deg[e.from] ?? 0) + 1
    if (ids.has(e.to)) deg[e.to] = (deg[e.to] ?? 0) + 1
  }
  for (const s of simsRef.current) s.deg = deg[s.id] ?? 0

  useEffect(() => {
    const cv = canvasRef.current!
    const ctx = cv.getContext("2d")!
    let stop = false
    let iter = 0
    let raf = 0

    const tick = () => {
      if (stop) return
      const w = cv.clientWidth
      if (cv.width !== w * devicePixelRatio || cv.height !== height * devicePixelRatio) {
        cv.width = w * devicePixelRatio
        cv.height = height * devicePixelRatio
      }
      ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0)
      ctx.clearRect(0, 0, w, height)
      const sims = simsRef.current
      if (sims.length === 0) { raf = requestAnimationFrame(tick); return }

      const { edges: es } = propsRef.current
      const liveEdges = es.filter((e) => ids.has(e.from) && ids.has(e.to))

      // physics — hot for the first 300 frames, then relax
      const k = iter < 300 ? 1 : 0.12
      iter++
      for (let i = 0; i < sims.length; i++) {
        for (let j = i + 1; j < sims.length; j++) {
          const a = sims[i], b = sims[j]
          let dx = b.x - a.x, dy = b.y - a.y
          let d2 = dx * dx + dy * dy
          if (d2 < 1) { d2 = 1; dx = Math.random(); dy = Math.random() }
          const d = Math.sqrt(d2)
          const f = (1600 / d2) * k
          a.vx -= (dx / d) * f; a.vy -= (dy / d) * f
          b.vx += (dx / d) * f; b.vy += (dy / d) * f
        }
      }
      for (const e of liveEdges) {
        const a = sims.find((s) => s.id === e.from)
        const b = sims.find((s) => s.id === e.to)
        if (!a || !b) continue
        const dx = b.x - a.x, dy = b.y - a.y
        const d = Math.sqrt(dx * dx + dy * dy) || 1
        const f = (d - 130) * 0.05 * k
        a.vx += (dx / d) * f; a.vy += (dy / d) * f
        b.vx -= (dx / d) * f; b.vy -= (dy / d) * f
      }
      for (const s of sims) {
        s.vx += (w / 2 - s.x) * 0.001
        s.vy += (height / 2 - s.y) * 0.001
        s.vx *= 0.82; s.vy *= 0.82
        s.x = Math.max(26, Math.min(w - 26, s.x + Math.max(-6, Math.min(6, s.vx))))
        s.y = Math.max(26, Math.min(height - 26, s.y + Math.max(-6, Math.min(6, s.vy))))
      }

      // edges
      ctx.lineWidth = 1
      for (const e of liveEdges) {
        const a = sims.find((s) => s.id === e.from)
        const b = sims.find((s) => s.id === e.to)
        if (!a || !b) continue
        ctx.strokeStyle = "rgba(136,153,187,0.18)"
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke()
      }

      // nodes + hover/picked highlight
      const hover = hoverRef.current
      let hovered: Sim | null = null
      for (const s of sims) {
        const r = Math.min(15, 4 + s.deg * 2 + (s.flags ?? 0) / 2)
        s.r = r
        const col = COLORS[s.type] ?? "#8899bb"
        const isPicked = pickedRef.current === s.id
        if (hover && Math.hypot(hover.x - s.x, hover.y - s.y) < r + 6) hovered = s
        ctx.fillStyle = col
        ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, Math.PI * 2); ctx.fill()
        if (isPicked || hovered === s) {
          ctx.strokeStyle = isPicked ? "rgba(0,255,136,0.9)" : "rgba(255,255,255,0.5)"
          ctx.lineWidth = 2
          ctx.beginPath(); ctx.arc(s.x, s.y, r + 5, 0, Math.PI * 2); ctx.stroke()
        }
        // labels for hubs + picked
        if (r >= 8 || isPicked) {
          ctx.fillStyle = "rgba(255,255,255,0.8)"
          ctx.font = "10px 'JetBrains Mono', monospace"
          ctx.textAlign = "center"
          ctx.fillText(s.label, s.x, s.y + r + 13)
        }
      }

      // tooltip drawn on canvas — no React state, no re-render
      if (hovered) {
        const label = `${hovered.label} · ${TYPE_LABELS[hovered.type] ?? hovered.type}`
        ctx.font = "11px 'JetBrains Mono', monospace"
        const tw = ctx.measureText(label).width
        let tx = hovered.x + 14, ty = hovered.y - 10
        if (tx + tw + 16 > w) tx = hovered.x - tw - 26
        ctx.fillStyle = "rgba(26,35,50,0.95)"
        ctx.strokeStyle = "rgba(255,255,255,0.12)"
        ctx.beginPath()
        ctx.roundRect(tx, ty, tw + 16, 22, 5)
        ctx.fill(); ctx.stroke()
        ctx.fillStyle = "#fff"
        ctx.textAlign = "left"
        ctx.fillText(label, tx + 8, ty + 15)
      }

      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => { stop = true; cancelAnimationFrame(raf) }
  }, [height])

  const locate = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  return (
    <div style={{ position: "relative" }}>
      <canvas
        ref={canvasRef}
        style={{ width: "100%", height, cursor: "pointer", display: "block" }}
        onMouseMove={(e) => { hoverRef.current = locate(e) }}
        onMouseLeave={() => { hoverRef.current = null }}
        onClick={(e) => {
          const m = locate(e)
          const hit = simsRef.current.find((s) => Math.hypot(s.x - m.x, s.y - m.y) < (s.r ?? 8) + 6)
          pickedRef.current = hit?.id ?? null
          if (hit && onPick) onPick(hit)
        }}
        aria-label="knowledge graph"
      />
      <div className="graph-legend small">
        {Object.entries(COLORS).map(([t, c]) => (
          <span key={t} style={{ color: c }}>● {TYPE_LABELS[t] ?? t}</span>
        ))}
      </div>
    </div>
  )
}
