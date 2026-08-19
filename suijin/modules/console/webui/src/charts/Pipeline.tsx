import { useState } from "react"

/** Red-team pipeline flow: Recon → Exploit → Escalate → Flag → Report. */
const STAGES = [
  { key: "recon", label: "Recon", detail: "nmap · gobuster · amass · fingerprinting" },
  { key: "exploit", label: "Exploit", detail: "sqlmap · msf · hand-rolled payloads" },
  { key: "escalate", label: "Escalate", detail: "privesc · GTFOBins · cred reuse" },
  { key: "flag", label: "Flag", detail: "claim_flag · verify evidence" },
  { key: "report", label: "Report", detail: "audit trail · mermaid chains" },
]

export default function Pipeline({ active = 0 }: { active?: number }) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <div className="pipeline" role="list">
      {STAGES.map((s, i) => {
        const state = i < active ? "done" : i === active ? "active" : "todo"
        return (
          <div className="pipe-wrap" key={s.key} role="listitem">
            <button
              className={`pipe-node ${state}`}
              onClick={() => setOpen(open === s.key ? null : s.key)}
              aria-expanded={open === s.key}
            >
              <span className="pipe-dot" />
              <span className="pipe-label">{s.label}</span>
              <span className="pipe-idx">{i + 1}</span>
            </button>
            {i < STAGES.length - 1 && <div className={`pipe-link ${i < active ? "flow" : ""}`} />}
            {open === s.key && <div className="pipe-detail mono">{s.detail}</div>}
          </div>
        )
      })}
    </div>
  )
}
