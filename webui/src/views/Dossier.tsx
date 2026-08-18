import { useState } from "react"
import { fetchDossier } from "../api"
import type { DossierData } from "../types"

export default function Dossier() {
  const [target, setTarget] = useState("")
  const [data, setData] = useState<DossierData | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  const search = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!target.trim()) return
    setLoading(true)
    setError("")
    setData(null)
    try {
      setData(await fetchDossier(target.trim()))
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err))
    } finally {
      setLoading(false)
    }
  }

  const rich = data
    ? Object.keys(data.constraints).length + data.failures.length +
      data.engagements.length + data.reports.length
    : 0

  return (
    <div className="grid" style={{ gap: 24 }}>
      <div className="view-head">
        <div>
          <h1>Target Dossier</h1>
          <div className="sub">
            Persistent per-target intel: knowledge-graph constraints, failed techniques,
            engagement history — merged from every artifact that mentions the target.
          </div>
        </div>
      </div>

      <form className="card" onSubmit={search}>
        <div style={{ display: "flex", gap: 12 }}>
          <input
            className="input"
            placeholder="IP / hostname / URL (e.g. 10.0.0.5, drfrost.org)"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            aria-label="target"
          />
          <button className="btn btn-primary" disabled={loading || !target.trim()}>
            {loading ? "building…" : "Build dossier"}
          </button>
        </div>
        {error && <div className="small" style={{ color: "var(--red)", marginTop: 10 }}>{error}</div>}
      </form>

      {data && (
        <>
          <div className="card">
            <div className="card-title">Dossier — {data.target}</div>
            <div className="small">intel richness: {rich} item(s) across 4 sources</div>
          </div>

          <div className="grid g2">
            <div className="card">
              <div className="card-title">Knowledge-graph constraints</div>
              {Object.keys(data.constraints).length === 0 && (
                <div className="small">(none recorded)</div>
              )}
              {Object.entries(data.constraints).map(([type, rules]) => (
                <div key={type} style={{ marginBottom: 12 }}>
                  <div className="mono" style={{ color: "var(--amber)" }}>{type}</div>
                  {rules.slice(0, 5).map((r, i) => (
                    <div key={i} className="small mono" style={{ marginLeft: 12 }}>{r}</div>
                  ))}
                </div>
              ))}
            </div>

            <div className="card">
              <div className="card-title">Failed techniques (avoid repeating)</div>
              {data.failures.length === 0 && <div className="small">(none)</div>}
              {data.failures.slice(0, 8).map((f, i) => (
                <div key={i} className="small mono" style={{ marginBottom: 6, color: "var(--red)" }}>{f}</div>
              ))}
            </div>
          </div>

          <div className="grid g2">
            <div className="card">
              <div className="card-title">Engagement history</div>
              {data.engagements.length === 0 && <div className="small">(first contact)</div>}
              {data.engagements.slice(0, 8).map((e, i) => (
                <div key={i} className="small mono" style={{ marginBottom: 6 }}>{e}</div>
              ))}
            </div>

            <div className="card">
              <div className="card-title">Reports mentioning target</div>
              {data.reports.length === 0 && <div className="small">(none)</div>}
              {data.reports.slice(0, 8).map((r, i) => (
                <div key={i} className="small mono" style={{ marginBottom: 6 }}>{r}</div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
