import { useEffect, useState } from "react"
import { fetchTimeline } from "../api"
import type { TimelineEvent } from "../types"

const KIND_COLORS: Record<string, string> = {
  "engagement start": "var(--red)",
  "engagement end": "var(--accent)",
  "session saved": "#7d9bff",
  report: "var(--amber)",
}

export default function Timeline() {
  const [events, setEvents] = useState<TimelineEvent[] | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    fetchTimeline(100)
      .then((d) => setEvents(d.events))
      .catch((e) => setError(String(e)))
  }, [])

  return (
    <div className="grid" style={{ gap: 24 }}>
      <div className="view-head">
        <div>
          <h1>Timeline</h1>
          <div className="sub">
            Unified chronological history across audit trails, saved sessions, and reports.
          </div>
        </div>
      </div>

      <div className="card" style={{ padding: 0 }}>
        {error && <div className="small" style={{ padding: 24, color: "var(--red)" }}>{error}</div>}
        {!events && !error && <div className="skeleton" style={{ height: 300, margin: 16 }} />}
        {events && events.length === 0 && (
          <div className="small" style={{ padding: 24 }}>
            No engagement history yet — run an engagement and artifacts will appear here.
          </div>
        )}
        {events && events.length > 0 && (
          <div style={{ maxHeight: 560, overflowY: "auto" }}>
            {events.map((e, i) => {
              const [day, clock] = e.ts.split(" ")
              const prev = i > 0 ? events[i - 1].ts.split(" ")[0] : null
              const showDay = day !== prev
              const color = KIND_COLORS[e.kind] ?? "var(--text-secondary)"
              return (
                <div key={i}>
                  {showDay && (
                    <div className="timeline-day small" style={{ paddingTop: i === 0 ? 16 : 14 }}>
                      {day}
                    </div>
                  )}
                  <div className="timeline-row">
                    <span className="mono dim">{clock}</span>
                    <span className="badge" style={{ background: "rgba(255,255,255,0.05)", color }}>
                      {e.kind}
                    </span>
                    <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                      {e.detail}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
