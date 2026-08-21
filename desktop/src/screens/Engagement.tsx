import { useMemo, useState } from "react";
import type { Gateway } from "../lib/gateway";

interface Step {
  stream: string;
  entry: Record<string, unknown>;
}

export function EngagementScreen({ gw, steps }: { gw: Gateway; steps: Step[] }) {
  const [objective, setObjective] = useState("");
  const [target, setTarget] = useState("");
  const [template, setTemplate] = useState("");
  const [launching, setLaunching] = useState(false);
  const [launched, setLaunched] = useState<string | null>(null);

  async function launch(e: React.FormEvent) {
    e.preventDefault();
    setLaunching(true);
    try {
      await gw.engage(objective, target, template);
      setLaunched(objective);
      setObjective("");
    } finally {
      setLaunching(false);
    }
  }

  const rows = useMemo(
    () =>
      steps.map((s, i) => {
        const e = s.entry ?? {};
        const tool = String(e.name ?? "—");
        const outcome = String(e.outcome ?? "");
        const err = outcome.startsWith("Error") || outcome.includes("exception");
        return (
          <div key={i} className={`stream-row ${err ? "err" : ""}`}>
            <span className="t">{new Date().toLocaleTimeString()}</span>
            <span className="tool">{tool}</span>
            <span className="out">{outcome.slice(0, 120) || "(no output)"}</span>
          </div>
        );
      }),
    [steps]
  );

  return (
    <div className="screen">
      <div style={{ maxWidth: 720 }}>
        <h1 className="h-display" style={{ fontSize: 24, marginBottom: 4 }}>
          Engagement
        </h1>
        <p className="muted small" style={{ marginBottom: 18 }}>
          Launch the red agent, watch every tool call stream live from the audit trail.
        </p>

        <form className="panel" onSubmit={launch} style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <div className="field" style={{ flex: 2, minWidth: 200, margin: 0 }}>
            <label>Objective</label>
            <input value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="Test the lab for SQLi and auth flaws" required />
          </div>
          <div className="field" style={{ flex: 1, minWidth: 120, margin: 0 }}>
            <label>Target</label>
            <input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="127.0.0.1:5906" />
          </div>
          <div className="field" style={{ width: 130, margin: 0 }}>
            <label>Template</label>
            <input value={template} onChange={(e) => setTemplate(e.target.value)} placeholder="external_web" />
          </div>
          <button className="btn primary" disabled={launching || !objective}>
            {launching ? "Starting…" : "Launch"}
          </button>
        </form>

        {launched && (
          <p className="small" style={{ color: "var(--cyan)", margin: "10px 0 0" }}>
            Launched: {launched} — switch to Approvals if the agent asks; stream appears below.
          </p>
        )}
      </div>

      <h2 className="h-display" style={{ fontSize: 18, margin: "26px 0 10px" }}>
        Live stream
        <span className="pill live" style={{ marginLeft: 10 }}>
          {steps.length} events
        </span>
      </h2>
      <div className="panel" style={{ padding: "8px 14px", maxHeight: "55%", overflowY: "auto" }}>
        {rows.length === 0 ? (
          <p className="muted small" style={{ padding: 8 }}>
            Waiting for agent activity… (launch an engagement, or one is already running)
          </p>
        ) : (
          <div className="stream">{rows}</div>
        )}
      </div>
    </div>
  );
}
