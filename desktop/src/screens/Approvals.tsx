import { useState } from "react";
import type { ApprovalItem, Gateway, QuestionItem } from "../lib/gateway";

export function ApprovalsScreen({
  gw,
  approvals,
  questions,
}: {
  gw: Gateway;
  approvals: ApprovalItem[];
  questions: QuestionItem[];
}) {
  const [busy, setBusy] = useState<number | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});

  async function decide(id: number, action: "approve" | "deny") {
    setBusy(id);
    try {
      await gw.decide(id, action);
    } finally {
      setBusy(null);
    }
  }

  async function answer(id: number) {
    setBusy(id);
    try {
      await gw.answer(id, answers[id] ?? "");
    } finally {
      setBusy(null);
    }
  }

  const pending = approvals.filter((a) => a.status === "pending");
  const resolved = approvals.filter((a) => a.status !== "pending");
  const openQs = questions.filter((q) => !q.answered);

  return (
    <div className="screen">
      <h1 className="h-display" style={{ fontSize: 24, marginBottom: 4 }}>
        Approvals
      </h1>
      <p className="muted small" style={{ marginBottom: 20 }}>
        The agent is blocked on your decision. Approving resumes it immediately.
      </p>

      <div className="hitl-stack">
        {pending.map((a) => {
          const args = (a.args ?? {}) as Record<string, unknown>;
          const cmd =
            String(args.cmd ?? args.command ?? a.command ?? "") ||
            JSON.stringify(args, null, 2);
          const danger = /\b(rm|mkfs|dd|shutdown|:(){|fork)\b/.test(cmd) || /\/8|\/16$/.test(cmd);
          return (
            <div key={a.id} className={`hitl ${danger ? "severity-danger" : ""}`}>
              <div className="hitl-q">{a.question ?? "Execute this command?"}</div>
              <div className="codeblock" style={{ marginBottom: 10 }}>
                {cmd || JSON.stringify(a, null, 2)}
              </div>
              <div className="hitl-meta">
                #{a.id}
                {danger && <span className="pill danger" style={{ marginLeft: 10 }}>high impact</span>}
              </div>
              <div className="hitl-actions">
                <button className="btn primary" disabled={busy === a.id} onClick={() => decide(a.id, "approve")}>
                  Approve
                </button>
                <button className="btn danger" disabled={busy === a.id} onClick={() => decide(a.id, "deny")}>
                  Deny
                </button>
              </div>
            </div>
          );
        })}

        {openQs.map((q) => (
          <div key={`q${q.id}`} className="hitl" style={{ borderLeftColor: "var(--cyan)" }}>
            <div className="hitl-q">{q.question}</div>
            <div className="hitl-meta">the agent asked for guidance</div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="field-input"
                style={{
                  flex: 1,
                  background: "var(--bg-sunken)",
                  border: "1px solid var(--line-strong)",
                  borderRadius: 6,
                  color: "var(--fg-bright)",
                  padding: "7px 12px",
                  outline: "none",
                  fontFamily: "var(--sans)",
                  fontSize: 13,
                }}
                placeholder="your answer…"
                value={answers[q.id] ?? ""}
                onChange={(e) => setAnswers((s) => ({ ...s, [q.id]: e.target.value }))}
                onKeyDown={(e) => e.key === "Enter" && answers[q.id] && answer(q.id)}
              />
              <button className="btn primary" disabled={busy === q.id || !answers[q.id]} onClick={() => answer(q.id)}>
                Answer
              </button>
            </div>
          </div>
        ))}

        {pending.length === 0 && openQs.length === 0 && (
          <div className="panel" style={{ textAlign: "center", padding: 40 }}>
            <div className="n-display" style={{ fontSize: 28, color: "var(--ok)" }}>
              Clear
            </div>
            <p className="muted small" style={{ marginTop: 6 }}>
              Nothing is waiting on you. The agent is unblocked.
            </p>
          </div>
        )}
      </div>

      {resolved.length > 0 && (
        <>
          <h2 className="h-display" style={{ fontSize: 18, margin: "28px 0 12px" }}>
            Resolved
          </h2>
          <table className="tbl">
            <thead>
              <tr>
                <th>#</th>
                <th>Command</th>
                <th>Decision</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {resolved.slice(-12).map((a) => (
                <tr key={a.id}>
                  <td>{a.id}</td>
                  <td className="code">{String(a.command ?? "—").slice(0, 60)}</td>
                  <td>
                    <span className={`pill ${a.status === "approved" ? "ok" : "danger"}`}>{a.status}</span>
                  </td>
                  <td className="muted small">{a.note ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
