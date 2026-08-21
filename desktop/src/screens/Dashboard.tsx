import { useEffect, useState } from "react";
import type { Gateway, Status, ToolInfo } from "../lib/gateway";

export function DashboardScreen({ gw, status }: { gw: Gateway; status: Status | null }) {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [usage, setUsage] = useState<Record<string, unknown> | null>(null);
  const [findings, setFindings] = useState<Record<string, Record<string, unknown>> | null>(null);

  useEffect(() => {
    gw.tools().then(setTools).catch(() => {});
    gw.usage().then(setUsage).catch(() => {});
    gw.findings().then(setFindings).catch(() => {});
  }, [gw]);

  const byOwner = tools.reduce<Record<string, number>>((acc, t) => {
    acc[t.owner || "?"] = (acc[t.owner || "?"] ?? 0) + 1;
    return acc;
  }, {});
  const topOwners = Object.entries(byOwner)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10);

  return (
    <div className="screen">
      <h1 className="h-display" style={{ fontSize: 24, marginBottom: 18 }}>
        Dashboard
      </h1>

      {status && (
        <div className="grid cols-4" style={{ marginBottom: 20 }}>
          <div className="panel">
            <div className="n-display" style={{ fontSize: 32 }}>
              {status.units}
            </div>
            <div className="muted small">units booted</div>
          </div>
          <div className="panel">
            <div className="n-display" style={{ fontSize: 32 }}>
              {status.tools}
            </div>
            <div className="muted small">tools live</div>
          </div>
          <div className="panel">
            <div className="n-display" style={{ fontSize: 32 }}>
              {status.kb_docs.toLocaleString()}
            </div>
            <div className="muted small">KB docs {status.kb_built ? "" : "(not built)"}</div>
          </div>
          <div className="panel">
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", paddingTop: 4 }}>
              <span className={`pill ${status.stealth ? "live" : ""}`}>{status.stealth ? "stealth on" : "stealth off"}</span>
              <span className="pill">{status.kg_backend}</span>
              <span className="pill">{status.provider}</span>
            </div>
            <div className="muted small" style={{ marginTop: 8 }}>
              posture
            </div>
          </div>
        </div>
      )}

      <div className="grid cols-2">
        <div className="panel">
          <h2 className="h-display" style={{ fontSize: 17, marginBottom: 10 }}>
            Spend
          </h2>
          {usage ? (
            <table className="tbl">
              <tbody>
                <tr>
                  <td>calls</td>
                  <td>{String(usage.calls)}</td>
                </tr>
                <tr>
                  <td>tokens in / out</td>
                  <td>
                    {Number(usage.input_tokens).toLocaleString()} / {Number(usage.output_tokens).toLocaleString()}
                  </td>
                </tr>
                <tr>
                  <td>estimated cost</td>
                  <td>${Number(usage.est_cost_usd).toFixed(4)}</td>
                </tr>
                <tr>
                  <td>accuracy</td>
                  <td className="muted small">
                    {String(usage.api_reported_calls)} api-reported · {String(usage.estimated_calls)} estimated
                  </td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="muted small">…</p>
          )}
        </div>

        <div className="panel">
          <h2 className="h-display" style={{ fontSize: 17, marginBottom: 10 }}>
            Arsenal
          </h2>
          <table className="tbl">
            <tbody>
              {topOwners.map(([owner, n]) => (
                <tr key={owner}>
                  <td>{owner}</td>
                  <td style={{ textAlign: "right" }}>{n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {findings && Object.keys(findings).length > 0 && (
        <div className="panel" style={{ marginTop: 14 }}>
          <h2 className="h-display" style={{ fontSize: 17, marginBottom: 10 }}>
            Knowledge graph — {Object.keys(findings).length} targets
          </h2>
          <table className="tbl">
            <thead>
              <tr>
                <th>Target</th>
                <th>Constraint types</th>
                <th>Entries</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(findings).map(([t, cons]) => {
                const types = Object.keys(cons).filter((k) => !k.startsWith("_"));
                const n = types.reduce((s, k) => s + ((cons[k] as unknown[])?.length ?? 0), 0);
                return (
                  <tr key={t}>
                    <td className="code">{t}</td>
                    <td className="muted">{types.join(", ")}</td>
                    <td>{n}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
