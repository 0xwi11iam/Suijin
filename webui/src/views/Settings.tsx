import { useEffect, useState } from "react"
import { fetchConfig } from "../api"
import { useStore } from "../store"

const SWATCHES: [string, string][] = [
  ["bg primary", "#0a0e17"],
  ["bg secondary", "#111927"],
  ["bg tertiary", "#1a2332"],
  ["accent", "#00ff88"],
  ["red team", "#ff3366"],
  ["blue team", "#3366ff"],
  ["warning", "#ffa500"],
  ["text muted", "#8899bb"],
]

export default function Settings() {
  const { snap } = useStore()
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  const [tab, setTab] = useState<"config" | "kb" | "design">("config")

  useEffect(() => {
    fetchConfig().then(setCfg).catch(() => setCfg(null))
  }, [])

  const kb = snap?.kb

  return (
    <div className="grid" style={{ gap: 24 }}>
      <div className="view-head">
        <div>
          <h1>Settings</h1>
          <div className="sub">Effective configuration (secrets redacted) · knowledge base · design tokens</div>
        </div>
        <div className="view-actions">
          <button className={`btn ${tab === "config" ? "btn-primary" : ""}`} onClick={() => setTab("config")}>Config</button>
          <button className={`btn ${tab === "kb" ? "btn-primary" : ""}`} onClick={() => setTab("kb")}>Knowledge Base</button>
          <button className={`btn ${tab === "design" ? "btn-primary" : ""}`} onClick={() => setTab("design")}>Design</button>
        </div>
      </div>

      {tab === "config" && (
        <div className="card">
          <div className="card-title">Effective suijin/config.json — validated, secrets redacted</div>
          {!cfg && <div className="skeleton" style={{ height: 300 }} />}
          {cfg && (
            <table className="table kv-table">
              <tbody>
                {Object.entries(cfg).map(([k, v]) => (
                  <tr key={k}>
                    <td className="mono">{k}</td>
                    <td className="mono">
                      {typeof v === "object" ? JSON.stringify(v) : String(v)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="small" style={{ marginTop: 12 }}>
            Edit via <span className="mono">suijin/config.json</span> or the Settings TUI — this console is read-only.
            Validate with <span className="mono">suijin config validate</span>.
          </div>
        </div>
      )}

      {tab === "kb" && (
        <div className="card">
          <div className="card-title">Knowledge Base</div>
          {kb?.built ? (
            <>
              <div className="display-stat">{(kb.docs ?? 0).toLocaleString()} <span style={{ fontSize: 18 }}>docs</span></div>
              <div className="small" style={{ marginBottom: 16 }}>
                built {kb.built_at?.slice(0, 10)}{kb.age_days != null ? ` · ${kb.age_days}d old` : ""}
              </div>
              <table className="table">
                <thead><tr><th>Source</th><th>Docs</th></tr></thead>
                <tbody>
                  {Object.entries(kb.sources ?? {}).map(([s, n]) => (
                    <tr key={s}><td className="mono">{s}</td><td className="mono">{n.toLocaleString()}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <div className="small">
              KB not built. Run <span className="mono">suijin pull kb</span> (optionally{" "}
              <span className="mono">--sources</span> to skip the ~300 MB SecLists pull).
            </div>
          )}
          <div className="small" style={{ marginTop: 12 }}>
            Failed sources retry with <span className="mono">suijin pull kb --sources &lt;name&gt;</span>.
            {snap?.kev && snap.kev.count > 0 && (
              <>
                {" "}KEV mirror: <span className="mono" style={{ color: "var(--red)" }}>
                  {snap.kev.count.toLocaleString()}
                </span>{" "}
                actively-exploited CVEs cached (<span className="mono">suijin pull cve</span>).
              </>
            )}
          </div>
        </div>
      )}

      {tab === "design" && (
        <div className="card">
          <div className="card-title">Abyss design tokens</div>
          <div className="swatch-row">
            {SWATCHES.map(([name, hex]) => (
              <div key={hex} style={{ textAlign: "center" }}>
                <div className="swatch" style={{ background: hex }} />
                <div className="small" style={{ marginTop: 6 }}>{name}</div>
                <div className="mono dim" style={{ fontSize: 10 }}>{hex}</div>
              </div>
            ))}
          </div>
          <div className="small" style={{ marginTop: 20 }}>
            Type: Gotham Medium (Montserrat fallback) · Instrument Serif display · JetBrains Mono.
            Console is read-only and bound to 127.0.0.1.
          </div>
        </div>
      )}
    </div>
  )
}
