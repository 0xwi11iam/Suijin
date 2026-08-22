import { useState, useEffect } from "react";
import { Gateway } from "../lib/gateway";

// Tauri fs bridge (present only in the native shell; undefined in browser dev)
declare global {
  interface Window {
    __TAURI__?: {
      core: { invoke: <T>(cmd: string) => Promise<T> };
    };
  }
}

export function Connect({
  onConnected,
}: {
  onConnected: (gw: { base: string; token: string }) => void;
}) {
  const [base, setBase] = useState("http://127.0.0.1:7331");
  const [token, setToken] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [detected, setDetected] = useState<string | null>(null);

  // one-click: read the discovery file through the Tauri shell
  useEffect(() => {
    const t = window.__TAURI__;
    if (!t) return;
    t.core
      .invoke<{ url?: string; token?: string } | null>("read_discovery")
      .then((d) => {
        if (d?.url && d?.token) {
          setDetected(d.url);
          setBase(d.url);
          setToken(d.token);
        }
      })
      .catch(() => {});
  }, []);

  async function go(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const g = new Gateway(base.replace(/\/$/, ""), token);
      await g.handshake();
      onConnected({ base: base.replace(/\/$/, ""), token });
    } catch (ex) {
      setErr(
        ex instanceof Error && ex.message.startsWith("401")
          ? "Invalid token — copy it from the gateway's startup line."
          : "Cannot reach the gateway — run: suijin gateway"
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="connect-wrap">
      <form className="connect-card" onSubmit={go}>
        <h1>
          Suijin<span style={{ color: "var(--cyan)" }}>.</span>
        </h1>
        <div className="sub">Connect to a running gateway</div>
        {detected && (
          <div
            className="pill live"
            style={{ display: "inline-block", marginBottom: 14 }}
          >
            gateway detected — ready
          </div>
        )}
        <div className="field">
          <label>Gateway</label>
          <input value={base} onChange={(e) => setBase(e.target.value)} spellCheck={false} />
        </div>
        <div className="field">
          <label>Session token</label>
          <input
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="from: suijin gateway"
            spellCheck={false}
            autoFocus
          />
        </div>
        <button className="btn primary" style={{ width: "100%", marginTop: 8 }} disabled={busy || !token}>
          {busy ? "Connecting…" : detected ? "Connect to detected gateway" : "Connect"}
        </button>
        <div className="connect-err">{err}</div>
      </form>
    </div>
  );
}
