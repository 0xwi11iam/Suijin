import { useEffect, useRef, useState } from "react"
import { useStore } from "../store"
import AttackMap from "../charts/AttackMap"
import Radar from "../charts/Radar"
import Sparkline from "../charts/Sparkline"
import type { TrafficEntry } from "../types"

function trafficScore(t: TrafficEntry): number {
  return Number((t as { ui_score?: number }).ui_score ?? 0)
}

function trafficKey(t: TrafficEntry, i: number): string {
  return `${t.timestamp ?? ""}|${t.method ?? ""}|${t.path ?? ""}|${i}`
}

function HeroStat({ label, value, sub, spark, color }: {
  label: string; value: string; sub?: string; spark?: number[]; color?: string
}) {
  return (
    <div className="card hero-card">
      <div className="card-title">{label}</div>
      <div className="hero-row">
        <div>
          <div className="display-stat" style={{ color: color ?? "var(--text-primary)" }}>{value}</div>
          {sub && <div className="small" style={{ marginTop: 4 }}>{sub}</div>}
        </div>
        {spark && spark.length > 1 && <Sparkline data={spark} color={color ?? "var(--accent)"} />}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const { snap, live } = useStore()
  const [spikes, setSpikes] = useState(0)
  const [feed, setFeed] = useState<TrafficEntry[]>([])
  const [rateHistory, setRateHistory] = useState<number[]>([])
  const countRef = useRef(0)
  const firstPaint = useRef(true)

  useEffect(() => {
    if (!snap) return
    const count = snap.traffic_count
    const prev = countRef.current
    if (firstPaint.current) {
      // seed once with the tail — no spiking on first paint
      setFeed(snap.traffic_recent.slice(-15))
      firstPaint.current = false
    } else if (count > prev) {
      const delta = Math.min(count - prev, snap.traffic_recent.length)
      const fresh = snap.traffic_recent.slice(-delta)
      setFeed((f) => [...f, ...fresh].slice(-100))
      if (fresh.some((t) => trafficScore(t) >= 4)) setSpikes((s) => s + delta)
    }
    countRef.current = count
    setRateHistory((r) => [...r.slice(-19), count])
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
  const suspect = (snap.traffic_recent ?? []).filter((t) => trafficScore(t) >= 4).length
  const kbDocs = snap.kb.built ? (snap.kb.docs ?? 0) : 0

  // radar from REAL detector signals (traffic window) + blue KG attack types
  const sig = snap.signal_counts ?? {}
  const atk = snap.blue_kg?.attack_type_counts ?? {}
  const radarAxes = [
    { label: "SQLi", keys: ["sql_injection", "sqli"] },
    { label: "XSS", keys: ["xss_attempt", "xss"] },
    { label: "CmdInj", keys: ["command_injection", "rce"] },
    { label: "Traversal", keys: ["path_traversal", "traversal"] },
    { label: "SSTI", keys: ["ssti_attempt", "ssti"] },
    { label: "XXE", keys: ["xxe_attempt", "xxe"] },
    { label: "Recon", keys: ["scanner_ua", "unusual_method", "recon"] },
    { label: "Bypass", keys: ["auth_bypass_header", "mass_assignment"] },
  ]
  const maxSig = Math.max(1, ...radarAxes.flatMap((a) => a.keys.map((k) => (sig[k] ?? 0) + (atk[k] ?? 0))))
  const radarData = radarAxes.map((a) => ({
    label: a.label,
    value: Math.min(1, 0.05 + a.keys.reduce((s, k) => s + (sig[k] ?? 0) + (atk[k] ?? 0), 0) / maxSig),
  }))
  const hasSignals = Object.keys(sig).length > 0 || Object.keys(atk).length > 0

  return (
    <div className="grid" style={{ gap: 24 }}>
      {/* Hero stats */}
      <div className="grid g4">
        <HeroStat
          label="Requests Monitored"
          value={snap.traffic_count.toLocaleString()}
          sub={live ? "live SSE stream" : "stream offline"}
          spark={rateHistory}
        />
        <HeroStat
          label="Suspect Requests"
          value={String(suspect)}
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
          color="var(--accent)"
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
            Vectors spawn when attack signals arrive on the blue-team feed — fire attacks at a lab
            (<span className="mono">suijin labs</span>) or run <span className="mono">suijin battle</span>.
          </div>
        </div>

        <div className="card">
          <div className="card-title">Attack Pattern Radar</div>
          <Radar data={radarData} />
          <div className="small">
            {hasSignals
              ? "Live detector signals from the traffic window + blue KG."
              : "No attack signals yet — start a lab and probe it, or run suijin battle."}
          </div>
        </div>
      </div>

      {/* Activity feed + labs */}
      <div className="grid g2">
        <div className="card" style={{ padding: 0 }}>
          <div className="card-title" style={{ padding: "20px 24px 0" }}>Real-time Activity</div>
          <div className="feed">
            {feed.length === 0 && (
              <div className="small" style={{ padding: 24 }}>
                No live traffic yet. Start the blue team (<span className="mono">suijin</span> → Blue Team)
                and probe the lab — events appear here in real time.
              </div>
            )}
            {feed.slice().reverse().map((t, i) => {
              const sc = trafficScore(t)
              const cls = sc >= 4 ? "badge-red" : sc >= 1 ? "badge-amber" : "badge-grey"
              const tier = sc >= 4 ? "SUSPECT" : sc >= 1 ? "ANOMALY" : "OK"
              return (
                <div className={`feed-item sev-${tier.toLowerCase()}`} key={trafficKey(t, i)}>
                  <span className="badge mono-dim">{String(t.timestamp ?? "").slice(11, 19)}</span>
                  <span className={`badge ${cls}`}>{tier}</span>
                  <span className="mono">{String(t.method ?? "?")}</span>
                  <span className="mono path">{String(t.path ?? "?")}</span>
                  <span className="mono dim ip">{String(t.ip ?? "")}</span>
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
          <div className="small" style={{ marginTop: 12 }}>
            Probe ports update every 3s via SSE.
          </div>
        </div>
      </div>
    </div>
  )
}
