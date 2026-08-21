import { useEffect, useMemo, useRef, useState } from "react";
import { Gateway, type ApprovalItem, type QuestionItem, type StreamFrame, type Status } from "../lib/gateway";
import { ApprovalsScreen } from "../screens/Approvals";
import { EngagementScreen } from "../screens/Engagement";
import { DashboardScreen } from "../screens/Dashboard";

const TABS = ["Approvals", "Engagement", "Dashboard"] as const;
type Tab = (typeof TABS)[number];

export function Shell({
  base,
  token,
  onDisconnect,
}: {
  base: string;
  token: string;
  onDisconnect: () => void;
}) {
  const gw = useMemo(() => new Gateway(base, token), [base, token]);
  const [tab, setTab] = useState<Tab>("Approvals");
  const [live, setLive] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [cost, setCost] = useState(0);
  const [costTick, setCostTick] = useState(false);
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [questions, setQuestions] = useState<QuestionItem[]>([]);
  const [steps, setSteps] = useState<{ stream: string; entry: Record<string, unknown> }[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  // initial snapshot + status refresh
  useEffect(() => {
    let dead = false;
    const refresh = async () => {
      try {
        const [s, a, q] = await Promise.all([gw.status(), gw.approvals(), gw.questions()]);
        if (dead) return;
        setStatus(s);
        setApprovals(a);
        setQuestions(q);
      } catch {
        /* the WS down-handler owns reconnection UX */
      }
    };
    refresh();
    const iv = setInterval(refresh, 10_000);
    return () => {
      dead = true;
      clearInterval(iv);
    };
  }, [gw]);

  // live stream
  useEffect(() => {
    const ws = gw.events(
      (f: StreamFrame) => {
        setLive(true);
        if (f.kind === "cost") {
          setCost(f.est_cost_usd);
          setCostTick(true);
          setTimeout(() => setCostTick(false), 400);
        } else if (f.kind === "approvals") setApprovals(f.items);
        else if (f.kind === "questions") setQuestions(f.items);
        else if (f.kind === "step") setSteps((prev) => [...prev.slice(-400), { stream: f.stream, entry: f.entry }]);
      },
      () => setLive(false)
    );
    wsRef.current = ws;
    return () => ws.close();
  }, [gw]);

  const pendingA = approvals.filter((a) => a.status === "pending").length;
  const pendingQ = questions.filter((q) => !q.answered).length;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          Suijin<span className="dot">.</span>
        </div>
        <div className={`conn ${live ? "live" : ""}`}>
          <span className="conn-dot" />
          {live ? "live" : "disconnected"}
        </div>
        <div className="topbar-spacer" />
        {status && (
          <span className="small muted">
            {status.units} units · {status.tools} tools · {status.provider}
            {status.stealth ? " · stealth" : ""}
          </span>
        )}
        <div className={`cost ${costTick ? "tick" : ""}`}>
          <span className="cur">$</span>
          {cost.toFixed(4)}
        </div>
        <button className="btn small" onClick={onDisconnect}>
          Disconnect
        </button>
      </header>

      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t}
            {t === "Approvals" && pendingA + pendingQ > 0 && <span className="badge">{pendingA + pendingQ}</span>}
          </button>
        ))}
      </nav>

      <main className="main">
        {tab === "Approvals" && (
          <ApprovalsScreen gw={gw} approvals={approvals} questions={questions} />
        )}
        {tab === "Engagement" && <EngagementScreen gw={gw} steps={steps} />}
        {tab === "Dashboard" && <DashboardScreen gw={gw} status={status} />}
      </main>

      <footer className="keybar">
        <span>
          <kbd>1-3</kbd> tabs
        </span>
        <span>
          <kbd>A</kbd> approve · <kbd>D</kbd> deny
        </span>
        <span style={{ flex: 1 }} />
        <span>{steps.length} events buffered</span>
      </footer>
    </div>
  );
}
