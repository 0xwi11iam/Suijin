import { useEffect, useState } from "react"
import { useStore } from "../store"
import { fetchReport } from "../api"

function fmtTime(mtime: number): string {
  return new Date(mtime * 1000).toLocaleString(undefined, {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  })
}

export default function Reports() {
  const { snap } = useStore()
  const reports = snap?.reports ?? []
  const audits = snap?.audits ?? []
  const [selected, setSelected] = useState<string | null>(null)
  const [content, setContent] = useState<string>("")
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!selected) return
    setLoading(true)
    fetchReport(selected)
      .then((r) => setContent(r.content))
      .catch(() => setContent("(failed to load)"))
      .finally(() => setLoading(false))
  }, [selected])

  return (
    <div className="grid" style={{ gap: 24 }}>
      <div className="view-head">
        <div>
          <h1>Reports &amp; Audit</h1>
          <div className="sub">Engagement artifacts from suijin_agent/reports and suijin_agent/audit_trails.</div>
        </div>
      </div>

      {/* Audit summaries */}
      <div className="card">
        <div className="card-title">Engagement Audits</div>
        <table className="table">
          <thead>
            <tr><th>Engagement</th><th>Started</th><th>Actions</th><th>Success</th><th>Failed</th><th>Findings</th><th>Cost</th></tr>
          </thead>
          <tbody>
            {audits.length === 0 && <tr><td colSpan={7} className="small">No audit trails yet.</td></tr>}
            {audits.map((a) => (
              <tr key={a.name}>
                <td className="mono">{a.engagement}</td>
                <td className="mono dim">{a.started?.slice(0, 16).replace("T", " ")}</td>
                <td className="mono">{a.actions}</td>
                <td className="mono" style={{ color: "var(--accent)" }}>{a.success}</td>
                <td className="mono" style={{ color: a.failed ? "var(--red)" : "var(--text-tertiary)" }}>{a.failed}</td>
                <td className="mono" style={{ color: a.findings ? "var(--amber)" : "var(--text-tertiary)" }}>{a.findings}</td>
                <td className="mono dim">${a.cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Report browser */}
      <div className="grid g-63">
        <div className="card" style={{ padding: 0 }}>
          <div className="card-title" style={{ padding: "20px 24px 0" }}>
            {selected ? selected.split("/").pop() : "Report Viewer"}
          </div>
          <div style={{ padding: 16 }}>
            {!selected && <div className="small">Select a report on the right.</div>}
            {loading && <div className="skeleton" style={{ height: 300 }} />}
            {!loading && selected && <div className="terminal md-body session-detail">{content}</div>}
          </div>
        </div>
        <div className="card" style={{ padding: 0 }}>
          <div className="card-title" style={{ padding: "20px 24px 0" }}>Files ({reports.length})</div>
          <div style={{ maxHeight: 460, overflowY: "auto", marginTop: 8 }}>
            {reports.length === 0 && <div className="small" style={{ padding: 24 }}>No reports yet.</div>}
            {reports.map((r) => (
              <div
                key={r.name}
                className={`feed-item report-list-item${selected === r.name ? " active-report" : ""}`}
                onClick={() => setSelected(r.name)}
              >
                <span className="mono dim">{fmtTime(r.mtime)}</span>
                <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
                <span className="small" style={{ marginLeft: "auto" }}>{(r.size / 1024).toFixed(0)} KB</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
