import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useBackendApi } from "@/api";
import { AppShell } from "@/components/app-shell";
import { MemoryMindmapPage } from "@/pages/memory-mindmap";
import { CommandPalette, type SearchMode, type SearchRequest } from "@/components/command-palette";
import { DashboardPanelProvider } from "@/components/dashboard-panel";
import { EmptyState, ErrorState, LoadingState } from "@/components/state-panel";
import { Button } from "@/components/ui/button";
import { FindingsPage } from "@/pages/findings-page";
import { LivePage } from "@/pages/live-page";
import { OverviewPage } from "@/pages/overview-page";
import { ScansPage } from "@/pages/scans-page";
import { ConsolePage } from "@/pages/console-page";
import { SavedConversationPage } from "@/pages/saved-conversation-page";
import { EngagementsPage } from "@/pages/engagements-page";
import { SwarmPage } from "@/pages/swarm-page";
import { DataPage } from "@/pages/data-page";
import { WorkflowsPage } from "@/pages/workflows-page";
import { FindingsApiAccessPage } from "@/pages/findings-api-access-page";
import { TeamSettingsPage } from "@/pages/team-settings-page";
import { SkillsPage } from "@/pages/skills-page";
import { WorkersPage } from "@/pages/workers-page";
import { WebConsoleControlsPage } from "@/pages/console-controls-page";
import { ModelsPage } from "@/pages/models-page";
import { canonicalWorkspacePath } from "@/lib/workspace-navigation";

function isTypingTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  return Boolean(element && (["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName) || element.isContentEditable));
}

export function OperationsApp() {
  const { client, getDashboard, getScans } = useBackendApi();
  const location = useLocation();
  const operationsVisible = /^\/(?:dashboard|operations|threads|findings|runs|scans|live)(?:\/|$)/.test(location.pathname);
  const searchReturnFocus = useRef<HTMLElement | null>(null);
  const rememberSearchFocus = () => { searchReturnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; };
  const [searchMode, setSearchMode] = useState<SearchMode>("all");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const dashboardQuery = useQuery({ queryKey: ["dashboard", client.backendId], queryFn: getDashboard, enabled: operationsVisible || paletteOpen, refetchInterval: 5000 });
  const scansQuery = useQuery({ queryKey: ["scans", client.backendId], queryFn: getScans, enabled: operationsVisible || paletteOpen, refetchInterval: 5000 });
  const handleHotkey = useEffectEvent((event: KeyboardEvent) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (!paletteOpen) rememberSearchFocus();
      setSearchMode("all");
      setPaletteOpen((value) => !value);
    } else if (!paletteOpen && event.key === "/" && !isTypingTarget(event.target)) {
      event.preventDefault();
      rememberSearchFocus();
      setSearchMode("all");
      setPaletteOpen(true);
    }
  });
  useEffect(() => {
    const listener = (event: KeyboardEvent) => handleHotkey(event);
    const openSearch = (event: Event) => {
      const request = (event as CustomEvent<SearchRequest>).detail;
      rememberSearchFocus();
      if (request.returnFocus) searchReturnFocus.current = request.returnFocus;
      setSearchMode(request.mode === "chats" ? "chats" : "all");
      setPaletteOpen(true);
    };
    window.addEventListener("keydown", listener);
    window.addEventListener("zero:open-search", openSearch);
    return () => { window.removeEventListener("keydown", listener); window.removeEventListener("zero:open-search", openSearch); };
  }, [handleHotkey]);
  // The memory mindmap is a FULLSCREEN takeover — no rail, no header, just a back button. It is
  // returned AFTER the hooks above: an early return before them made the hook order depend on the
  // route, which React refuses to render when leaving the map.
  if (location.pathname.startsWith("/memory")) return <MemoryMindmapPage />;

  const operations = (content: ReactNode) => {
    if (dashboardQuery.isLoading || scansQuery.isLoading) return <LoadingState label="Loading" />;
    const error = dashboardQuery.error ?? scansQuery.error;
    if (error) return <ErrorState error={error} />;
    return content;
  };
  const compatibilityPage = <Navigate to={{ pathname: canonicalWorkspacePath(location.pathname), search: location.search, hash: location.hash }} replace />;
  const dashboard = dashboardQuery.data;
  const scans = scansQuery.data ?? [];
  const startChat = (
    <Button asChild variant="accent" size="sm">
      <NavLink to="/chats">Start a chat</NavLink>
    </Button>
  );
  const findings = operations(dashboard ? <FindingsPage dashboard={dashboard} /> : <EmptyState title="No findings yet" action={startChat} />);
  return (
    <DashboardPanelProvider>
      <AppShell>
        <CommandPalette returnFocus={searchReturnFocus} mode={searchMode} onModeChange={setSearchMode} open={paletteOpen} onOpenChange={setPaletteOpen} dashboard={dashboard} scans={scansQuery.data} />
        <Routes>
          <Route path="/" element={<Navigate to="/chats?new=1" replace />} />
          <Route path="/chats" element={<ConsolePage />} />
          <Route path="/chats/:sessionId" element={<ConsolePage />} />
          <Route path="/chats/saved/:savedId" element={<SavedConversationPage />} />
          {["/setup", "/console/setup", "/console/onboarding"].map(path => <Route key={path} path={path} element={<Navigate to={{ pathname: "/onboarding", search: location.search, hash: location.hash }} replace />} />)}
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/connections" element={<Navigate to="/models" replace />} />
          {["onboarding", "settings", "plugins", "doctor", "tools", "project", "fix"].map((route) => (
            <Route key={route} path={`/${route}`} element={<WebConsoleControlsPage />} />
          ))}
          <Route path="/automations" element={<WorkflowsPage />} />
          {["workflows", "audits", "launcher", "launch"].map(route => <Route key={route} path={`/${route}`} element={compatibilityPage} />)}
          <Route path="/api-access" element={<FindingsApiAccessPage />} />
          <Route path="/team" element={<TeamSettingsPage />} />
          <Route path="/plugins/skills" element={<SkillsPage />} />
          <Route path="/learning" element={<Navigate to="/memory" replace />} />
          <Route path="/memory" element={<MemoryMindmapPage />} />
          <Route path="/engagements" element={<EngagementsPage />} />
          <Route path="/engagements/:id" element={<EngagementsPage />} />
          <Route path="/scope" element={<Navigate to="/engagements" replace />} />
          <Route path="/scope/:id" element={<Navigate to="/engagements" replace />} />
          <Route path="/swarm" element={<SwarmPage />} />
          <Route path="/data" element={<DataPage />} />
          <Route path="/dashboard" element={operations(dashboard ? <OverviewPage data={dashboard} /> : <EmptyState title="Nothing here yet" action={startChat} />)} />
          <Route path="/operations" element={compatibilityPage} />
          <Route path="/threads" element={compatibilityPage} />
          <Route path="/threads/:fingerprint" element={compatibilityPage} />
          <Route path="/findings" element={findings} />
          <Route path="/engines" element={<WorkersPage />} />
          <Route path="/findings/:fingerprint" element={findings} />
          <Route path="/runs" element={operations(<ScansPage scans={scans} />)} />
          <Route path="/runs/:scanId" element={operations(<ScansPage scans={scans} />)} />
          {["console", "workers", "skills"].map(route => <Route key={route} path={`/${route}`} element={compatibilityPage} />)}
          {["console", "workers", "skills"].map(route => <Route key={`${route}-child`} path={`/${route}/*`} element={compatibilityPage} />)}
          <Route path="/scans" element={compatibilityPage} />
          <Route path="/scans/:scanId" element={compatibilityPage} />
          <Route path="/live" element={<LivePage />} />
          <Route path="*" element={<EmptyState title="Page not found" body="Press Cmd+K to find a page." />} />
        </Routes>
      </AppShell>
    </DashboardPanelProvider>
  );
}
