import { NavLink, Route, Routes } from "react-router-dom"
import { useStore } from "./store"
import Dashboard from "./views/Dashboard"
import RedTeam from "./views/RedTeam"
import BlueTeam from "./views/BlueTeam"
import Labs from "./views/Labs"
import Reports from "./views/Reports"
import Settings from "./views/Settings"
import Graph from "./views/Graph"

const NAV = [
  { to: "/", icon: "◈", label: "Dashboard", end: true },
  { to: "/red", icon: "⚔", label: "Red Team" },
  { to: "/blue", icon: "🛡", label: "Blue Team" },
  { to: "/graph", icon: "◉", label: "Knowledge Graph" },
  { to: "/labs", icon: "⚗", label: "Labs" },
  { to: "/reports", icon: "▤", label: "Reports" },
  { to: "/settings", icon: "⚙", label: "Settings" },
]

function Sidebar() {
  const { mode, setMode, snap } = useStore()
  return (
    <aside className="sidebar">
      <div className="logo float" title="Medusa">
        <svg width="34" height="34" viewBox="0 0 34 34" fill="none">
          <circle cx="17" cy="17" r="14" stroke="var(--accent)" strokeWidth="1.5" opacity="0.5" />
          <circle cx="17" cy="17" r="8" stroke="var(--accent)" strokeWidth="1.5" />
          <circle cx="17" cy="17" r="3" fill="var(--accent)" />
          <path d="M17 3 v6 M17 25 v6 M3 17 h6 M25 17 h6" stroke="var(--accent)" strokeWidth="1.5" />
        </svg>
      </div>
      <nav>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end as never} className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
            <span className="nav-icon">{n.icon}</span>
            <span className="nav-label">{n.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="mode-toggle" role="radiogroup" aria-label="Team mode">
          <button
            className={`mode-btn ${mode === "red" ? "on-red" : ""}`}
            onClick={() => setMode("red")}
            title="Red Team — offense"
          >
            ⚔
          </button>
          <button
            className={`mode-btn ${mode === "blue" ? "on-blue" : ""}`}
            onClick={() => setMode("blue")}
            title="Blue Team — defense"
          >
            🛡
          </button>
        </div>
        <div className="nav-item small" title={snap?.version ?? ""}>
          <span className="nav-icon">v</span>
          <span className="nav-label">{snap?.version ?? "…"}</span>
        </div>
      </div>
    </aside>
  )
}

function TopBar() {
  const { live, mode, snap } = useStore()
  const modeLabel = mode === "red" ? "RED TEAM" : "BLUE TEAM"
  return (
    <header className="topbar">
      <div className={`mode-pill ${mode}`}>
        <span className="dot pulse" />
        {modeLabel}
      </div>
      <div className="crumbs" aria-live="polite">
        {snap ? (
          <>
            {snap.provider.name}
            {snap.provider.model ? ` · ${snap.provider.model}` : ""}
            {snap.kb.built ? ` · KB ${snap.kb.docs!.toLocaleString()} docs` : " · KB not built"}
          </>
        ) : (
          "connecting…"
        )}
      </div>
      <div className="top-right">
        <span className={`badge ${live ? "badge-green" : "badge-grey"}`} title={live ? "SSE stream connected" : "stream down — polling"}>
          {live ? "● LIVE" : "○ offline"}
        </span>
      </div>
    </header>
  )
}

export default function App() {
  return (
    <div className="shell">
      <Sidebar />
      <div className="main-col">
        <TopBar />
        <main className="content fade-in">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/red" element={<RedTeam />} />
            <Route path="/blue" element={<BlueTeam />} />
            <Route path="/graph" element={<Graph />} />
            <Route path="/labs" element={<Labs />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}
