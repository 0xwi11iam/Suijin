import { useEffect, useRef } from "react"

/**
 * Abstract attack map — dotted world grid with animated attack vectors.
 * Canvas-based; spawns a new vector each time `spikes` increments.
 */
interface Props {
  spikes: number
  height?: number
}

interface AtkVec { x1: number; y1: number; x2: number; y2: number; t: number }

// Equirectangular lat/lon -> fractional canvas coords
function project(lat: number, lon: number): [number, number] {
  return [(lon + 180) / 360, (90 - lat) / 180]
}

const HOT_SPOTS: [number, number][] = [
  [37.77, -122.42], [40.71, -74.01], [51.5, -0.13], [52.52, 13.4],
  [35.68, 139.69], [1.35, 103.82], [-33.87, 151.21], [55.75, 37.62],
  [19.43, -99.13], [-23.55, -46.63], [25.2, 55.27], [48.86, 2.35],
]
const HOME: [number, number] = [37.77, -122.42]

export default function AttackMap({ spikes, height = 400 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const vecsRef = useRef<AtkVec[]>([])
  const spikesRef = useRef(spikes)
  const rafRef = useRef(0)

  if (spikes !== spikesRef.current) {
    spikesRef.current = spikes
    // spawn 1-3 vectors per spike
    const count = 1 + Math.floor(Math.random() * 3)
    for (let i = 0; i < count; i++) {
      const dst = HOT_SPOTS[Math.floor(Math.random() * HOT_SPOTS.length)]
      const src = HOT_SPOTS[Math.floor(Math.random() * HOT_SPOTS.length)]
      const [x1, y1] = project(src[0], src[1])
      const [x2, y2] = project(...(dst === src ? HOME : dst))
      vecsRef.current = [...vecsRef.current, { x1, y1, x2, y2, t: 0 }]
    }
    if (vecsRef.current.length > 24) vecsRef.current = vecsRef.current.slice(-24)
  }

  useEffect(() => {
    const cv = canvasRef.current!
    const ctx = cv.getContext("2d")!
    let stop = false

    const draw = () => {
      if (stop) return
      const w = cv.clientWidth
      const h = height
      if (cv.width !== w * devicePixelRatio || cv.height !== h * devicePixelRatio) {
        cv.width = w * devicePixelRatio
        cv.height = h * devicePixelRatio
      }
      ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0)
      ctx.clearRect(0, 0, w, h)

      // dotted grid "world"
      const cols = 64
      const rows = 30
      const gx = w / cols
      const gy = h / rows
      ctx.fillStyle = "rgba(136,153,187,0.22)"
      for (let c = 0; c < cols; c++) {
        for (let r = 0; r < rows; r++) {
          // rough continent mask via value noise-ish hash
          const v = Math.sin(c * 12.9898 + r * 78.233) * 43758.5453
          const frac = v - Math.floor(v)
          if (frac > 0.62) ctx.fillRect(c * gx, r * gy, 1.4, 1.4)
        }
      }

      // home node
      const [hx, hy] = project(HOME[0], HOME[1])
      ctx.fillStyle = "rgba(0,255,136,0.9)"
      ctx.beginPath()
      ctx.arc(hx * w, hy * h, 3.5, 0, Math.PI * 2)
      ctx.fill()
      const pulseR = 6 + ((performance.now() / 600) % 1) * 16
      ctx.strokeStyle = `rgba(0,255,136,${0.5 * (1 - (pulseR - 6) / 16)})`
      ctx.beginPath()
      ctx.arc(hx * w, hy * h, pulseR, 0, Math.PI * 2)
      ctx.stroke()

      // vectors
      const vecs = vecsRef.current
      for (const v of vecs) {
        v.t += 0.008
        if (v.t > 1.6) continue
        const prog = Math.min(1, v.t)
        const ax = v.x1 * w
        const ay = v.y1 * h
        const bx = v.x2 * w
        const by = v.y2 * h
        const mx = (ax + bx) / 2
        const my = Math.min(ay, by) - 40

        // quadratic curve path
        const path = new Path2D()
        path.moveTo(ax, ay)
        path.quadraticCurveTo(mx, my, bx, by)
        const alpha = v.t < 1 ? 1 : 1 - (v.t - 1) / 0.6
        ctx.strokeStyle = `rgba(255,51,102,${0.55 * alpha})`
        ctx.lineWidth = 1.2
        ctx.stroke(path)

        // traveling particle
        const q = 1 - prog
        const px = q * q * ax + 2 * q * prog * mx + prog * prog * bx
        const py = q * q * ay + 2 * q * prog * my + prog * prog * by
        ctx.fillStyle = `rgba(255,80,120,${alpha})`
        ctx.beginPath()
        ctx.arc(px, py, 2.4, 0, Math.PI * 2)
        ctx.fill()

        // impact ring
        if (prog >= 1) {
          ctx.strokeStyle = `rgba(255,51,102,${0.4 * alpha})`
          ctx.beginPath()
          ctx.arc(bx, by, 4 + (1 - alpha) * 14, 0, Math.PI * 2)
          ctx.stroke()
        }
      }
      vecsRef.current = vecs.filter((v) => v.t <= 1.6)

      rafRef.current = requestAnimationFrame(draw)
    }
    rafRef.current = requestAnimationFrame(draw)
    return () => {
      stop = true
      cancelAnimationFrame(rafRef.current)
    }
  }, [height])

  return <canvas ref={canvasRef} style={{ width: "100%", height }} aria-label="live attack map" />
}
