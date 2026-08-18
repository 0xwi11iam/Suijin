import { useMemo, useState } from "react"
import { useStore } from "../store"
import Pipeline from "../charts/Pipeline"

export default function RedTeam() {
  const { snap } = useStore()
  const [tab, setTab] = useState<"log" | "findings">("log")
  const audits = snap?.audits ?? []
  const latest = audits[0]

  // Reconstruct a terminal-style log from the newest audit trail view of data
  const logLines = useMemo(() => {
    const lines: { text: string; cls: string }[] = []
    for (const a of audits.slice(0, 6)) {
      lines.push({ text: `# engagement: ${a.engagement}  (${a.started?.slice(0, 19)})`, cls: "t-dim" })
      lines.push({ text: `# actions: ${a.actions} | success ${a.success} | failed ${a.failed} | findings ${a.findings} | $${a.cost_usd.toFixed(4)}`, cls: "t-dim" })
      lines.push({ text: "", cls: "" })
    }
    if (lines.length === 0) lines.push({ text: "No audit trails yet — run an engagement from the TUI (suijin → Red Team).", cls: "t-amber" })
    return lines
  }, [audits])

  const findings = useMemo(() => {
    const out: { eng: string; text: string }[] = []
    for (const a of audits) {
      if (a.findings > 0) out.push({ eng: a.engagement, text: `${a.findings} finding(s) recorded` })
    }
    return out
  }, [audits])

  const missing = snap?.tools.missing ?? {}
  const missingList = Object.entries(missing).map(([tool, bins]) => ({ tool, bins }))

  // derive active pipeline stage heuristically from latest audit ratios
  const stage = useMemo(() => {
    if (!latest || latest.actions === 0) return 0
    const done = latest.success / Math.max(1, latest.actions)
    if (latest.findings > 0) return 4
    if (done > 0.85) return 3
    if (done > 0.6) return 2
    if (done > 0.3) return 1
    return 0
  }, [latest])

  return (
    <div className="grid" style={{ gap: 24 }}>
      <div className="view-head">
        <div>
          <h1>Red Team <span style={{ color: "var(--red)" }}>—</span> Offensive Operations</h1>
          <div className="sub">Autonomous attack pipeline · LangGraph state machine · subagents · zero-cost supervisor</div>
        </div>
        <div className="view-actions">
          <span className="badge badge-red pulse">● ENGAGEMENT READY</span>
        </div>
      </div>

      <div className="card scan-frame">
        <div className="card-title">Attack Pipeline</div>
        <Pipeline active={stage} />
        <div style={{ height: 34 }} />
      </div>

      <div className="grid g-63">
        <div className="card" style={{ padding: 0 }}>
          <div style={{ display: "flex", gap: 8, padding: "18px 20px 0" }}>
            <button className={`btn ${tab === "log" ? "btn-primary" : ""}`} onClick={() => setTab("log")}>Engagement Log</button>
            <button className={`btn ${tab === "findings" ? "btn-primary" : ""}`} onClick={() => setTab("findings")}>Findings</button>
          </div>
          {tab === "log" ? (
            <div className="terminal" style={{ margin: 16, maxHeight: 420 }}>
              {logLines.map((l, i) => (
                <div key={i} className={l.cls}>{l.text || "\u00A0"}</div>
              ))}
            </div>
          ) : (
            <div style={{ padding: 16, maxHeight: 420, overflowY: "auto" }}>
              {findings.length === 0 && <div className="small">No findings recorded yet. The agent logs verified findings via <span className="mono">record_finding</span>.</div>}
              {findings.map((f, i) => (
                <div key={i} className="tarpit-row">
                  <span className="badge badge-amber">FINDING</span>
                  <span className="mono">{f.eng}</span>
                  <span className="small" style={{ marginLeft: "auto" }}>{f.text}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-title">Tool Arsenal</div>
          <div className="tool-grid">
            {["nmap", "gobuster", "feroxbuster", "amass", "subfinder", "whatweb", "sslscan", "httpx"].map((t) => {
              const miss = missingList.find((m) => m.tool === t)
              return (
                <div key={t} className="tool-chip" title={miss ? `missing: ${miss.bins.join(", ")}` : "ready"}>
                  <span>{t}</span>
                  {miss
                    ? <span className="badge badge-grey">missing</span>
                    : <span className="badge badge-green">ready</span>}
                </div>
              )
            })}
          </div>
          <div className="small" style={{ marginTop: 14 }}>
            {snap ? `${snap.tools.module_tool_count} module tools loaded. ` : ""}
            Missing binaries unlock via brew/apt — see <span className="mono">suijin doctor</span>.
          </div>
        </div>
      </div>

      <div className="grid g4">
        <div className="card">
          <div className="card-title">Subagents</div>
          <div className="display-stat">3</div>
          <div className="small">max concurrent · 5 steps · 95s hard cap</div>
        </div>
        <div className="card">
          <div className="card-title">Supervisor</div>
          <div className="display-stat">0</div>
          <div className="small">interventions this session (loop / missed-flag / stall detection)</div>
        </div>
        <div className="card">
          <div className="card-title">KB Offline</div>
          <div className="display-stat" style={{ color: snap?.kb.built ? "var(--accent)" : "var(--text-tertiary)" }}>
            {snap?.kb.built ? (snap.kb.docs ?? 0).toLocaleString() : "—"}
          </div>
          <div className="small">{snap?.kb.built ? "docs indexed · search_kb armed" : "run: suijin pull kb"}</div>
        </div>
        <div className="card">
          <div className="card-title">Failure Memory</div>
          <div className="display-stat">{snap?.sessions_count ?? 0}</div>
          <div className="small">saved sessions · mine_failures avoids repeats</div>
        </div>
      </div>
    </div>
  )
}
