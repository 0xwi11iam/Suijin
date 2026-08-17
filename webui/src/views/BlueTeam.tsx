import { useState } from "react"
import { useStore } from "../store"

const DETECTORS = [
  "SQL Injection", "SQLi (Blind)", "XSS", "Path Traversal", "SSRF",
  "Command Injection", "SSTI", "XXE", "JWT Attack", "Deserialization",
  "LDAP Injection", "NoSQL Injection", "Scanner UA", "Mass Assignment",
  "Auth Bypass Hdr", "Brute Force", "File Inclusion", "GraphQL Attack",
]

export default function BlueTeam() {
  const { snap } = useStore()
  const [delay, setDelay] = useState(5)

  const tarpit = snap?.tarpit ?? {}
  const tarpitIps = Object.entries(tarpit)
  const traffic = snap?.traffic_recent ?? []
  const kg = snap?.blue_kg

  // detector "hits" are visual placeholders until per-pattern counts ship in the KG
  const attackTypes = kg?.nodes.filter((n) => n.type === "attack") ?? []
  const hits = new Set(attackTypes.map((n) => String(n.data.attack_type ?? n.data.type ?? "unknown")))

  return (
    <div className="grid" style={{ gap: 24 }}>
      <div className="view-head">
        <div>
          <h1>Blue Team <span style={{ color: "#7d9bff" }}>—</span> Autonomous Defense</h1>
          <div className="sub">18 pre-AI detectors · AI decision engine · deception over blocking · per-endpoint subagents</div>
        </div>
        <div className="view-actions">
          {kg
            ? <span className="badge badge-blue pulse">● SESSION ACTIVE</span>
            : <span className="badge badge-grey">no active session</span>}
        </div>
      </div>

      {/* Traffic monitor */}
      <div className="card" style={{ padding: 0 }}>
        <div className="card-title" style={{ padding: "20px 24px 0" }}>Traffic Monitor — three-tier pipeline</div>
        <div style={{ maxHeight: 300, overflowY: "auto", marginTop: 8 }}>
          {traffic.length === 0 && (
            <div className="small" style={{ padding: 24 }}>
              No live traffic. Start Blue Team (<span className="mono">medusa</span> → Blue Team → built-in lab) and attack
              from another terminal — requests stream here over SSE.
            </div>
          )}
          {traffic.slice().reverse().map((t, i) => {
            const sc = Number((t as { ui_score?: number }).ui_score ?? 0)
            const tier = sc >= 4 ? "INVESTIGATED" : sc >= 1 ? "ANOMALOUS" : "NORMAL"
            const badge = tier === "INVESTIGATED" ? "badge-red" : tier === "ANOMALOUS" ? "badge-amber" : "badge-grey"
            return (
              <div className="feed-item" key={i}>
                <span className={`badge ${badge}`}>{tier}</span>
                <span className="mono">{String(t.method ?? "?")} {String(t.path ?? "?")}</span>
                <span className="mono dim" style={{ marginLeft: "auto" }}>{String(t.ip ?? "")}</span>
                <span className={`badge ${badge}`}>{sc}/10</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Detector grid */}
      <div className="card">
        <div className="card-title">Attack Detectors — 18 signatures, threshold 5</div>
        <div className="det-grid">
          {DETECTORS.map((d) => {
            const hit = hits.size > 0 && [...hits].some((h) => d.toLowerCase().includes(h.toLowerCase().split(" ")[0]))
            return (
              <div key={d} className={`det-card${hit ? " hit" : ""}`}>
                <div className="det-name">{d}</div>
                <div className="det-count" style={{ color: hit ? "var(--red)" : "var(--text-tertiary)" }}>
                  {hit ? "●" : "·"}
                </div>
              </div>
            )
          })}
        </div>
        <div className="small" style={{ marginTop: 12 }}>
          Cards light up when the live knowledge graph records a matching attack type. Repeat offenders gain +1 effective score per flag.
        </div>
      </div>

      {/* Deception + KG summary */}
      <div className="grid g2">
        <div className="card">
          <div className="card-title">Deception Arsenal</div>
          <div className="small" style={{ marginBottom: 10 }}>Tarpit delay slider (seconds) — written live to <span className="mono">/tmp/blue_tarpit.json</span></div>
          <div className="tarpit-row">
            <span className="mono dim">delay</span>
            <input type="range" min={1} max={15} value={delay} onChange={(e) => setDelay(Number(e.target.value))} />
            <span className="display-stat" style={{ fontSize: 22 }}>{delay}s</span>
          </div>
          <table className="table" style={{ marginTop: 12 }}>
            <thead><tr><th>Tarpitted IP</th><th>Delay</th><th>Until</th></tr></thead>
            <tbody>
              {tarpitIps.length === 0 && <tr><td colSpan={3} className="small">No IPs currently tarpitted.</td></tr>}
              {tarpitIps.map(([ip, cfg]) => (
                <tr key={ip}>
                  <td className="mono">{ip}</td>
                  <td className="mono">{String((cfg as { delay?: number }).delay ?? "?")}s</td>
                  <td className="mono dim">{String((cfg as { until?: string }).until ?? "session")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="small" style={{ marginTop: 10 }}>
            Network blocks (score 8+) deploy via pfctl/iptables from the decision engine — the UI is read-only over them.
          </div>
        </div>

        <div className="card">
          <div className="card-title">Session Knowledge Graph</div>
          {kg ? (
            <>
              <table className="table">
                <thead><tr><th>Node Type</th><th>Count</th></tr></thead>
                <tbody>
                  {Object.entries(kg.node_counts).map(([t, n]) => (
                    <tr key={t}><td className="mono">{t}</td><td className="mono">{n}</td></tr>
                  ))}
                </tbody>
              </table>
              <div className="small" style={{ marginTop: 12 }}>
                {kg.nodes.length} nodes rendered on the <a href="#/graph">graph view</a> · shared across subagents · resets per session.
              </div>
            </>
          ) : (
            <div className="small">No active blue session. Start one to build the graph.</div>
          )}
        </div>
      </div>
    </div>
  )
}
