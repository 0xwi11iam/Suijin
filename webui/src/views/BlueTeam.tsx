import { useState } from "react"
import { useStore } from "../store"

// detector label -> detector/ KG signal keys it lights up for
const DETECTORS: { label: string; keys: string[] }[] = [
  { label: "SQL Injection", keys: ["sql_injection", "sqli"] },
  { label: "SQLi (Blind)", keys: ["sql_injection_blind", "blind_sqli"] },
  { label: "XSS", keys: ["xss_attempt", "xss"] },
  { label: "Path Traversal", keys: ["path_traversal", "traversal"] },
  { label: "SSRF", keys: ["ssrf_attempt", "ssrf"] },
  { label: "Command Injection", keys: ["command_injection", "rce"] },
  { label: "SSTI", keys: ["ssti_attempt", "ssti"] },
  { label: "XXE", keys: ["xxe_attempt", "xxe"] },
  { label: "JWT Attack", keys: ["jwt_attack", "jwt"] },
  { label: "Deserialization", keys: ["deserialization", "deserialize"] },
  { label: "LDAP Injection", keys: ["ldap_injection", "ldap"] },
  { label: "NoSQL Injection", keys: ["nosql_injection", "nosql"] },
  { label: "Scanner UA", keys: ["scanner_ua", "scanner"] },
  { label: "Mass Assignment", keys: ["mass_assignment"] },
  { label: "Auth Bypass Hdr", keys: ["auth_bypass_header", "auth_bypass"] },
  { label: "Brute Force", keys: ["brute_force"] },
  { label: "File Inclusion", keys: ["file_inclusion", "lfi"] },
  { label: "GraphQL Attack", keys: ["graphql_attack", "graphql"] },
]

export default function BlueTeam() {
  const { snap } = useStore()
  const [delay, setDelay] = useState(5)

  const tarpit = snap?.tarpit ?? {}
  const tarpitIps = Object.entries(tarpit)
  const traffic = snap?.traffic_recent ?? []
  const kg = snap?.blue_kg

  const sig = snap?.signal_counts ?? {}
  const atk = kg?.attack_type_counts ?? {}
  const countFor = (keys: string[]) =>
    keys.reduce((s, k) => s + (sig[k] ?? 0) + (atk[k] ?? 0), 0)
  const totalHits = DETECTORS.reduce((s, d) => s + countFor(d.keys), 0)

  return (
    <div className="grid" style={{ gap: 24 }}>
      <div className="view-head">
        <div>
          <h1>Blue Team <span style={{ color: "#7d9bff" }}>—</span> Autonomous Defense</h1>
          <div className="sub">18 pre-AI detectors · AI decision engine · deception over blocking · per-endpoint subagents</div>
        </div>
        <div className="view-actions">
          {totalHits > 0
            ? <span className="badge badge-red pulse">● {totalHits} HITS</span>
            : <span className="badge badge-grey">no hits yet</span>}
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
            const signals = ((t as { ui_signals?: string[] }).ui_signals ?? []).slice(0, 3).join(", ")
            return (
              <div className={`feed-item sev-${tier.toLowerCase()}`} key={`${t.timestamp ?? ""}-${i}`}>
                <span className={`badge ${badge}`}>{tier}</span>
                <span className="mono">{String(t.method ?? "?")} {String(t.path ?? "?")}</span>
                {signals && <span className="mono dim signal">{signals}</span>}
                <span className="mono dim ip" style={{ marginLeft: "auto" }}>{String(t.ip ?? "")}</span>
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
            const n = countFor(d.keys)
            return (
              <div key={d.label} className={`det-card${n > 0 ? " hit" : ""}`}>
                <div className="det-name">{d.label}</div>
                <div className="det-count" style={{ color: n > 0 ? "var(--red)" : "var(--text-tertiary)" }}>
                  {n > 0 ? n : "·"}
                </div>
              </div>
            )
          })}
        </div>
        <div className="small" style={{ marginTop: 12 }}>
          Counts aggregate live detector signals (traffic window) and blue-KG attack records.
          Repeat offenders gain +1 effective score per flag.
        </div>
      </div>

      {/* Deception + KG summary */}
      <div className="grid g2">
        <div className="card">
          <div className="card-title">Deception Arsenal</div>
          <div className="small" style={{ marginBottom: 10 }}>
            Tarpit delay preview (seconds) — the decision engine writes live values to <span className="mono">/tmp/blue_tarpit.json</span>
          </div>
          <div className="tarpit-row">
            <span className="mono dim">delay</span>
            <input type="range" min={1} max={15} value={delay} onChange={(e) => setDelay(Number(e.target.value))} />
            <span className="display-stat" style={{ fontSize: 22 }}>{delay}s</span>
          </div>
          <table className="table" style={{ marginTop: 12 }}>
            <thead><tr><th>Tarpitted IP</th><th>Delay</th></tr></thead>
            <tbody>
              {tarpitIps.length === 0 && <tr><td colSpan={2} className="small">No IPs currently tarpitted.</td></tr>}
              {tarpitIps.map(([ip, cfg]) => (
                <tr key={ip}>
                  <td className="mono">{ip}</td>
                  <td className="mono">{String((cfg as { delay?: number }).delay ?? "?")}s</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="small" style={{ marginTop: 10 }}>
            Network blocks (score 8+) deploy via pfctl/iptables from the decision engine — this console is read-only over them.
          </div>
        </div>

        <div className="card">
          <div className="card-title">Session Knowledge Graph</div>
          {kg && Object.keys(kg.node_counts).length > 0 ? (
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
