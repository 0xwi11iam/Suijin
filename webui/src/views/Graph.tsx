import { useMemo, useState } from "react"
import { useStore } from "../store"
import ForceGraph, { type GNode } from "../charts/ForceGraph"

export default function Graph() {
  const { snap } = useStore()
  const [picked, setPicked] = useState<GNode | null>(null)
  const [source, setSource] = useState<"blue" | "red">("blue")

  const blue = useMemo(() => {
    const kg = snap?.blue_kg
    if (!kg) return { nodes: [] as GNode[], edges: [] as { from: string; to: string }[] }
    const nodes: GNode[] = kg.nodes.map((n) => ({
      id: n.id,
      type: n.type,
      label: String(n.data.ip ?? n.data.path ?? n.data.attack_type ?? n.id.slice(0, 12)),
      flags: Number(n.data.flags ?? n.data.score ?? 0),
    }))
    const edges = kg.edges.map((e) => ({
      from: String(e.from ?? e.source ?? ""),
      to: String(e.to ?? e.target ?? ""),
    }))
    return { nodes, edges }
  }, [snap])

  const red = useMemo(() => {
    const rkg = snap?.red_kg ?? {}
    const nodes: GNode[] = Object.entries(rkg).map(([target, groups]) => {
      const total = Object.values(groups as Record<string, unknown[]>)
        .reduce((a, v) => a + (Array.isArray(v) ? v.length : 0), 0)
      return { id: target, type: "endpoint", label: target, flags: total }
    })
    return { nodes, edges: [] as { from: string; to: string }[] }
  }, [snap])

  const data = source === "blue" ? blue : red
  const hasBlue = (snap?.blue_kg?.nodes.length ?? 0) > 0

  return (
    <div className="grid" style={{ gap: 24 }}>
      <div className="view-head">
        <div>
          <h1>Knowledge Graph</h1>
          <div className="sub">
            {source === "blue"
              ? "Session-scoped blue intel: attackers, attacks, defenses, endpoints — shared by every subagent."
              : "Persistent red constraints: per-target blocked patterns, WAF rules, verified CVEs."}
          </div>
        </div>
        <div className="view-actions">
          <button className={`btn ${source === "blue" ? "btn-primary" : ""}`} onClick={() => setSource("blue")}>
            Blue ({blue.nodes.length})
          </button>
          <button className={`btn ${source === "red" ? "btn-primary" : ""}`} onClick={() => setSource("red")}>
            Red ({red.nodes.length})
          </button>
        </div>
      </div>

      <div className="card scan-frame" style={{ padding: 0 }}>
        {data.nodes.length === 0 ? (
          <div className="small" style={{ padding: 32 }}>
            {source === "blue" && !hasBlue
              ? "No blue session graph yet — start Blue Team and generate traffic."
              : "Graph is empty for this source."}
          </div>
        ) : (
          <ForceGraph nodes={data.nodes} edges={data.edges} onPick={setPicked} />
        )}
      </div>

      {picked && (
        <div className="card fade-in">
          <div className="card-title">Node — {picked.label}</div>
          <table className="table kv-table">
            <tbody>
              <tr><td>id</td><td className="mono">{picked.id}</td></tr>
              <tr><td>type</td><td className="mono">{picked.type}</td></tr>
              <tr><td>label</td><td className="mono">{picked.label}</td></tr>
              {picked.flags !== undefined && <tr><td>weight</td><td className="mono">{picked.flags}</td></tr>}
            </tbody>
          </table>
          <button className="btn" style={{ marginTop: 14 }} onClick={() => setPicked(null)}>Close</button>
        </div>
      )}
    </div>
  )
}
