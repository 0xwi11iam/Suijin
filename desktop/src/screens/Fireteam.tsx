import { useEffect, useState } from "react";
import type { Gateway } from "../lib/gateway";

export interface FireteamTask {
  task: string;
  state: "running" | "done" | "queued";
  success: boolean | null;
  steps: number | null;
  findings: string;
}

export interface FireteamTeam {
  team_id: string;
  started: string;
  running: number;
  tasks: FireteamTask[];
}

export function FireteamScreen({
  gw,
  snapshot,
}: {
  gw: Gateway;
  snapshot: { teams: FireteamTeam[] } | null;
}) {
  const [teams, setTeams] = useState<FireteamTeam[]>(snapshot?.teams ?? []);

  // poll fallback (WS pushes updates, but initial load + reconnects poll)
  useEffect(() => {
    if (snapshot?.teams) setTeams(snapshot.teams);
  }, [snapshot]);

  useEffect(() => {
    const iv = setInterval(() => {
      gw
        .fireteam()
        .then((d) => setTeams(d.teams ?? []))
        .catch(() => {});
    }, 5000);
    return () => clearInterval(iv);
  }, [gw]);

  return (
    <div className="screen">
      <h1 className="h-display" style={{ fontSize: 24, marginBottom: 4 }}>
        Fireteam
      </h1>
      <p className="muted small" style={{ marginBottom: 20 }}>
        Parallel specialists deployed by the agent. Results arrive as they land — the agent
        picks them up automatically on its next turn.
      </p>

      {teams.length === 0 && (
        <div className="panel" style={{ textAlign: "center", padding: 40 }}>
          <div className="n-display" style={{ fontSize: 28 }}>
            No teams
          </div>
          <p className="muted small" style={{ marginTop: 6 }}>
            The agent deploys specialists with action=deploy_subagent when tasks are worth
            parallelizing.
          </p>
        </div>
      )}

      {teams.map((t) => (
        <div key={t.team_id} className="panel" style={{ marginBottom: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
            <span className="n-display" style={{ fontSize: 18 }}>
              {t.team_id}
            </span>
            {t.running > 0 ? (
              <span className="pill live">{t.running} running</span>
            ) : (
              <span className="pill ok">complete</span>
            )}
            <span className="muted small">{new Date(t.started).toLocaleString()}</span>
          </div>
          {t.tasks.map((task, i) => (
            <div
              key={i}
              className="firetask"
              data-state={task.state}
              style={{
                display: "flex",
                gap: 12,
                alignItems: "flex-start",
                padding: "10px 14px",
                marginBottom: 6,
                background: "var(--bg-sunken)",
                borderRadius: 6,
                border: "1px solid var(--line)",
                borderLeft: `3px solid ${
                  task.state === "running"
                    ? "var(--cyan)"
                    : task.success === true
                      ? "var(--ok)"
                      : task.success === false
                        ? "var(--danger)"
                        : "var(--fg-faint)"
                }`,
              }}
            >
              <span
                className="pill"
                style={{
                  flexShrink: 0,
                  borderColor:
                    task.state === "running"
                      ? "var(--cyan-dim)"
                      : task.success === true
                        ? "rgba(52,211,153,0.4)"
                        : task.success === false
                          ? "rgba(248,113,113,0.4)"
                          : "var(--line-strong)",
                  color:
                    task.state === "running"
                      ? "var(--cyan)"
                      : task.success === true
                        ? "var(--ok)"
                        : task.success === false
                          ? "var(--danger)"
                          : "var(--fg-dim)",
                }}
              >
                {task.state === "running" ? "live" : task.state}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ color: "var(--fg)" }}>{task.task}</div>
                {task.state === "done" && task.findings && (
                  <div
                    className="codeblock"
                    style={{ marginTop: 8, fontSize: 11.5, maxHeight: 120, overflowY: "auto" }}
                  >
                    {task.findings}
                  </div>
                )}
              </div>
              {task.steps != null && (
                <span className="muted small" style={{ flexShrink: 0 }}>
                  {task.steps} steps
                </span>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
