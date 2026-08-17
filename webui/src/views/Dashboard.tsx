import { useEffect, useRef, useState } from "react"
import { useStore } from "../store"
import AttackMap from "../charts/AttackMap"
import Radar from "../charts/Radar"
import Sparkline from "../charts/Sparkline"
import type { TrafficEntry } from "../types"

function HeroStat({ label, value, sub, spark, color }: {
  label: string; value: string; sub?: string; spark?: number[]; color?: string
}) {
  return (
    <div className="card">
      <div className="card-title">{label}</div>
      <div className="hero-row">
        <div>
          <div className="display-stat" style={{ color: color ?? "var(--text-primary)" }}>{value}</div>
          {sub && <div className="small" style={{ marginTop: 4 }}>{sub}</div>}
        </div>
        {spark && <Sparkline data={spark} color={color ?? "var(--accent)"} />}
      </div>
    </div>
  )
}

function trafficScore(t: TrafficEntry): number {
  return Number((t as { ui_score?: number }).ui_score ?? 0)
}

export default function Dashboard() {
  const { snap, live } = useStore()
  const [spikes, setSpikes] = useState(0)
  const [feed, setFeed] = useState<TrafficEntry[]>([])
  const trafficCountRef = useRef(0)
  const feedRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!snap) return
    if (snap.traffic_count > trafficCountRef.current) {
      const delta = snap.traffic_count - trafficCountRef.current
      if (trafficCountRef.current > 0 && delta > 0 && snap.traffic_recent.some((t) => trafficScore(t) >= 4)) {
        setSpikes((s) => s + delta)
      }
      trafficCountRef.current = snap.traffic_count
      setFeed((f) => [...snap.traffic_recent.slice(-40), ...f].slice(0, 80))
    }
  }, [snap])

  if (!snap) {
    return (
      <div className="grid g4">
        {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 140 }} />)}
      </div>
    )
  }

  const audits = snap.audits ?? []
  const totalActions = audits.reduce((a, x) => a + x.actions, 0)
  const totalFindings = audits.reduce((a, x) => a + x.findings, 0)
  const totalCost = audits.reduce((a, x) => a + x.cost_usd, 0)
  const attacked = (snap.traffic_recent ?? []).filter((t) => trafficScore(t) >= 4).length
  const kbDocs = snap.kb.built ? (snap.kb.docs ?? 0) : 0

  // radar categories derived from blue KG node counts
  const nc = snap.blue_kg?.node_counts ?? {}
  const radarData = [
    { label: "SQLi", value: nc.attack ? 0.9 : 0.1 },
    { label: "XSS", value: nc.attack ? 0.6 : 0.05 },
    { label: "CmdInj", value: nc.attack ? 0.5 : 0.05 },
    { label: "Traversal", value: nc.attack ? 0.4 : 0.05 },
    { label: "Recon", value: nc.attacker ? 0.8 : 0.05 },
    { label: "Deception", value: (snap.tarpit && Object.keys(snap.tarpit).length) ? 0.7 : 0.05 },
    { label: "Blocked", value: nc.defense ? 0.6 : 0.05 },
  ]

  return (
    <div className="grid" style={{ gap: 24 }}>
      {/* Hero stats */}
      <div className="grid g4">
        <HeroStat
          label="Requests Monitored"
          value={snap.traffic_count.toLocaleString()}
          sub={live ? "live SSE stream" : "stream offline"}
        />
        <HeroStat
          label="Suspect Requests"
          value={String(attacked)}
          sub="attack signal in recent window"
          color="var(--red)"
        />
        <HeroStat
          label="Engagement Findings"
          value={String(totalFindings)}
          sub={`${totalActions} actions across ${audits.length} audits`}
        />
        <HeroStat
          label="API Cost (audits)"
          value={`$${totalCost.toFixed(2)}`}
          sub={`KB ${kbDocs.toLocaleString()} docs · ${snap.tools.module_tool_count} tools`}
        />
      </div>

      {/* Attack map + radar */}
      <div className="grid g-63">
        <div className="card scan-frame" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "20px 24px 0" }}>
            <div className="card-title" style={{ marginBottom: 0 }}>Live Attack Surface</div>
            <span className={`badge ${live ? "badge-green" : "badge-grey"}`}>
              {live ? "SSE CONNECTED" : "OFFLINE"}
            </span>
          </div>
          <AttackMap spikes={spikes} />
          <div className="small" style={{ padding: "0 24px 16px" }}>
            Vectors spawn when attack signals arrive on the blue-team feed.
          </div>
        </div>

        <div className="card">
          <div className="card-title">Attack Pattern Radar</div>
          <Radar data={radarData} />
          <div className="small">
            Derived from the live blue knowledge graph{snap.blue_kg ? "" : " (no session active — start Blue Team)"}.
          </div>
        </div>
      </div>

      {/* Activity feed + labs */}
      <div className="grid g2">
        <div className="card" style={{ padding: 0 }}>
          <div className="card-title" style={{ padding: "20px 24px 0" }}>Real-time Activity</div>
          <div className="feed" ref={feedRef}>
            {feed.length === 0 && (
              <div className="small" style={{ padding: 24 }}>
                No live traffic yet. Start the blue team (<span className="mono">medusa</span> → Blue Team)
                and probe the lab — events appear here in real time.
              </div>
            )}
            {feed.map((t, i) => {
              const sc = trafficScore(t)
              const cls = sc >= 4 ? "badge-red" : sc >= 1 ? "badge-amber" : "badge-grey"
              return (
                <div className="feed-item" key={i}>
                  <span className="mono dim">{String(t.timestamp ?? "").slice(11, 19)}</span>
                  <span className={`badge ${cls}`}>{cls === "badge-red" ? "SUSPECT" : cls === "badge-amber" ? "ANOMALY" : "OK"}</span>
                  <span className="mono">{String(t.method ?? "?").padEnd(5, " ")}</span>
                  <span className="mono" style={{ color: "var(--text-primary)" }}>{String(t.path ?? "?")}</span>
                  <span className="mono dim" style={{ marginLeft: "auto" }}>{String(t.ip ?? "")}</span>
                </div>
              )
            })}
          </div>
        </div>

        <div className="card">
          <div className="card-title">Lab Fleet</div>
          <table className="table">
            <thead>
              <tr><th>Lab</th><th>Port</th><th>Status</th></tr>
            </thead>
            <tbody>
              {snap.labs.map((l) => (
                <tr key={l.name}>
                  <td className="mono">{l.name}</td>
                  <td className="mono dim">{l.port ? `:${l.port}` : "?"}</td>
                  <td>
                    {l.running
                      ? <span className="badge badge-green">● RUNNING</span>
                      : <span className="badge badge-grey">stopped</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
