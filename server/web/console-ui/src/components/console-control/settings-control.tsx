import { Link, useLocation } from "react-router-dom";
import type { ConsoleExecutionStatus } from "@0/engine/shared";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useWebTheme } from "@/lib/web-theme";
import { useTeamAccess } from "@/components/team-access";
import { useBackendApi } from "@/api";
import { Disclosure } from "@/components/ui/disclosure";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Feedback, QueryState, SubmitButton, jsonBody } from "./control-ui";
import { SettingsRow, SettingsSection } from "./settings-section";
import { Input } from "@/components/ui/input";
import { ProviderIcon } from "@/components/provider-icon";
import type { SettingDefinition, SettingsResponse } from "./contracts";

export function useConsoleSettings() {
  const { client, webFetchJson } = useBackendApi();
  const team = useTeamAccess();
  const queryKey = ["console-settings", client.backendId, team.user?.workspaceId, team.user?.userId] as const;
  return { ...useQuery({ queryKey, queryFn: ({ signal }) => webFetchJson<SettingsResponse>("/api/console/settings", { signal }) }), queryKey };
}

export { settingsCategories, categoryFor, settingLabel, visibleWebSetting, type SettingsCategory } from "./web-settings";
import { settingsCategories, categoryFor, settingLabel, visibleWebSetting, webSettingDetails, settingChoiceLabel, sharedSettingKeys, type SettingsCategory } from "./web-settings";

const MODEL_KEYS = new Set(["routing_worker_model", "model_1", "model_2", "model_3"]);
const stringValue = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));

function ModelSelect({ definition, value, disabled, onChange }: { definition: SettingDefinition; value: string; disabled: boolean; onChange: (v: string) => void }) {
  const { webFetchJson } = useBackendApi();
  const list = useQuery({
    queryKey: ["available-models"],
    queryFn: () => webFetchJson<{ models: { value: string; label: string; providerLabel: string }[]; providers: { key: string; label: string }[] }>("/api/console/available-models"),
    staleTime: 300_000,
    retry: 1,
  });
  const options = list.data?.models ?? [];
  const providerFromPrefix = (list.data?.providers ?? []).find(pr => value.startsWith(`${pr.key}:`));
  const selected = options.find(m => m.value === value
    || (value.length > 0 && m.value.endsWith(":" + value))
    || m.label === value);
  const selectedProvider = selected && (list.data?.providers ?? []).find(pr => pr.label === selected.providerLabel);
  const routeId = providerFromPrefix?.key ?? selectedProvider?.key ?? "";
  const routeLabel = providerFromPrefix?.label ?? selectedProvider?.label ?? (selected?.providerLabel ?? "");
  // PORTALLED dropdown — the settings control column is only 12rem wide and
  // card surfaces clip absolutely-positioned panels, so the list renders into
  // <body> with fixed coordinates: 22rem wide, scrolls, opens below the input
  // (flips above when there is no room). The native <datalist> this replaces
  // could not scroll at all.
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ right: number; top: number; bottom: number } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const syncAnchor = () => {
    const r = boxRef.current?.getBoundingClientRect();
    if (r) setAnchor({ right: r.right, top: r.bottom, bottom: window.innerHeight - r.bottom });
  };
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (boxRef.current && !boxRef.current.contains(e.target as Node) && !(e.target as Element)?.closest?.("[data-model-panel]")) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onScroll = () => syncAnchor();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onScroll);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); window.removeEventListener("resize", onScroll); };
  }, [open]);
  const toggle = () => { if (!open) { syncAnchor(); setPanelQuery(""); } setOpen(o => !o); };
  // The panel shows ALL connected providers' models (511+) — filtering only
  // happens in the panel's own search box, never from the typed model value.
  const [panelQuery, setPanelQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) searchRef.current?.focus(); }, [open]);
  const q = panelQuery.trim().toLowerCase();
  const matches = options.filter(m => !q || `${m.value} ${m.providerLabel} ${m.label}`.toLowerCase().includes(q));
  const panelStyle = anchor ? (() => {
    const width = 352;
    const left = Math.max(8, Math.min(anchor.right - width, window.innerWidth - width - 8));
    const below = anchor.bottom - 12;
    if (below >= 160) return { left, top: anchor.top + 4, width, maxHeight: Math.min(320, below) };
    const above = anchor.top - window.innerHeight + anchor.bottom - anchor.top; // space above = top edge y
    const spaceAbove = anchor.top - 12;
    return { left, bottom: window.innerHeight - anchor.top + 4, width, maxHeight: Math.min(320, Math.max(160, spaceAbove)) };
  })() : { left: 8, top: 8, width: 352, maxHeight: 320 };
  return <div className="space-y-1" ref={boxRef}>
    <div className="flex gap-1.5">
      <Input value={value} onChange={e => onChange(e.target.value)} disabled={disabled}
        placeholder="provider:model — pick from the list or type it" className="w-full" autoComplete="off" />
      <Button type="button" variant="outline" size="icon" aria-label="Show models" aria-expanded={open}
        disabled={disabled || options.length === 0} onClick={toggle} className="shrink-0">
        <ChevronDown aria-hidden="true" className="size-4" />
      </Button>
    </div>
    {open && createPortal(
      <div role="listbox" data-model-panel aria-label={`${definition.label} models`} style={panelStyle}
        className="ui-menu-surface fixed z-[80] flex flex-col overflow-hidden rounded-[var(--radius-menu,12px)] border border-border bg-popover text-popover-foreground shadow-[var(--ui-panel-shadow,0_8px_24px_rgb(0_0_0_/_16%))]" >
        <div className="shrink-0 border-b border-border/60 p-1.5">
          <Input ref={searchRef} value={panelQuery} onChange={e => setPanelQuery(e.target.value)}
            placeholder={`Search ${options.length} models…`} className="h-8 w-full text-sm" autoComplete="off" />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1">
        {matches.length === 0
          ? <p className="px-3 py-2 text-xs text-muted-foreground">{options.length ? "No models match." : "No models available."}</p>
          : matches.map(m => <button key={m.value} type="button" role="option" aria-selected={m.value === value}
              onClick={() => { onChange(m.value); setOpen(false); }}
              className={`block w-full rounded-[var(--radius-control,8px)] px-2.5 py-1.5 text-left ${m.value === value ? "bg-muted" : "hover:bg-muted/60"}`}>
              <span className="block w-full truncate text-sm leading-5">{m.label}</span>
              <span className="block w-full truncate text-xs leading-4 text-muted-foreground">{m.providerLabel}</span>
            </button>)}
        </div>
      </div>, document.body)}
    <p className="flex items-center gap-1.5 text-xs leading-4 text-muted-foreground">
      {routeLabel && <ProviderIcon providerId={routeId} className="size-3 shrink-0" />}
      {routeLabel ? `via ${routeLabel}` : list.isPending ? "Fetching connected providers' models…"
        : list.isError ? "Model list unreachable — type a provider:model id."
        : "Pick a provider:model from the list, or type one"}
    </p>
  </div>;
}

function SettingRow({ definition, value, scope, pending, apply }: { definition: SettingDefinition; value: unknown; scope: "global" | "project"; pending: boolean; apply: (key: string, value: unknown, scope: "global" | "project") => void }) {
  const label = settingLabel(definition);
  const description = webSettingDetails(definition.key)?.description ?? "";
  const enabledChoice = definition.kind === "enum" && definition.choices?.length === 2 && definition.choices.includes("off") ? definition.choices.find(choice => choice !== "off") : undefined;
  const isToggle = definition.kind === "boolean" || enabledChoice !== undefined;
  const applyValue = (next: unknown) => apply(definition.key, next, definition.operatorOnly ? "global" : scope);
  return <SettingsRow id={`setting-${definition.key}`} label={label} description={description} badge={definition.operatorOnly ? "This engine" : undefined}>
    {isToggle ? <Switch aria-label={label} checked={definition.kind === "boolean" ? value === true : value === enabledChoice} disabled={pending} onCheckedChange={next => applyValue(definition.kind === "boolean" ? next : next ? enabledChoice : "off")} /> : MODEL_KEYS.has(definition.key)
      ? <ModelSelect definition={definition} value={stringValue(value)} disabled={pending} onChange={applyValue} />
      : <Select aria-label={label} className="w-full" value={stringValue(value)} onValueChange={applyValue} disabled={pending} options={definition.choices?.map(choice => ({ value: choice, label: settingChoiceLabel(definition.key, choice) })) ?? []} />}
  </SettingsRow>;
}

function ExecutionControl() {
  const { client, webFetchJson } = useBackendApi();
  const status = useQuery({ queryKey: ["console-execution", client.backendId], queryFn: ({ signal }) => webFetchJson<ConsoleExecutionStatus>("/api/console/execution", { signal }), refetchInterval: 5000 });
  const execution = status.data;
  const isolated = execution?.profile === "smolvm";
  const ready = Boolean(execution && (!isolated || execution.configured && execution.runtimeReady && execution.imageApproved && execution.platformSupported !== false));
  return <div className="space-y-3 py-4">
    <QueryState pending={status.isPending} error={status.error} retry={status.refetch} />
    {execution && <>
      <div className="space-y-1"><p className="flex flex-wrap items-center justify-between gap-2 text-sm"><span className="font-medium">Tool environment</span><span className="text-xs text-muted-foreground">{ready ? "Ready" : "Needs setup"}</span></p>{!ready && <p role="status" className="max-w-lg text-xs leading-5 text-muted-foreground">{execution.platformSupported === false ? "Isolated workspaces are unavailable on this host. Choose another host or run tools directly on this host." : "Finish configuring an isolated workspace, or choose a host that already has one."}</p>}</div>
      {isolated && !ready && <div className="flex flex-wrap gap-2"><Button asChild size="sm" variant="outline"><Link to="/doctor">Check setup</Link></Button><Button asChild size="sm" variant="ghost"><Link to="/engines">Choose host</Link></Button></div>}
      <Disclosure summary="Runtime details" triggerClassName="rounded-[var(--radius-control)] px-1 py-2 text-xs text-muted-foreground hover:text-foreground" contentClassName="space-y-3 px-1 pb-2"><dl className="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-[8rem_minmax(0,1fr)]">
        {isolated && <><dt className="text-muted-foreground">Workspace setup</dt><dd>{execution.configured ? "Configured" : "Not configured"}</dd><dt className="text-muted-foreground">Runtime</dt><dd>{execution.runtimeReady ? "Available" : "Unavailable"}</dd><dt className="text-muted-foreground">Image</dt><dd>{execution.imageApproved ? "Approved" : "Not approved"}</dd></>}
        {execution.workspace && <><dt className="text-muted-foreground">Workspace</dt><dd className="break-all">{execution.workspace}</dd></>}
        {execution.imageDigest && <><dt className="text-muted-foreground">Image digest</dt><dd className="break-all">{execution.imageDigest}</dd></>}
        {execution.resources && <><dt className="text-muted-foreground">Resources</dt><dd>{execution.resources.cpus} CPUs · {execution.resources.memoryMb} MiB memory · {execution.resources.storageGb} GiB storage</dd></>}
      </dl>{(execution.error || execution.message) && <p className="text-xs leading-5 text-muted-foreground">{execution.error || execution.message}</p>}</Disclosure>
    </>}
  </div>;
}

export function SettingsControl({ presentationOnly = false, category = "general" }: { presentationOnly?: boolean; category?: SettingsCategory }) {
  const { webFetchJson } = useBackendApi();
  const settings = useConsoleSettings();
  const [appearance, setAppearance] = useWebTheme();
  const queryClient = useQueryClient();
  const location = useLocation();
  const scope = "global" as const;
  const [resetKeys, setResetKeys] = useState<string[] | null>(null);
  const resetTriggerRef = useRef<HTMLElement | null>(null);
  const openReset = (keys: string[]) => { reset.reset(); resetTriggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; setResetKeys(keys); };
  const [message, setMessage] = useState<string | null>(null);
  const save = useMutation({ mutationFn: async (input: { key: string; value: unknown; scope: "global" | "project" }) => {
    if (!visibleWebSetting(input)) throw new Error("This preference is not managed by the web app.");
    const data = await webFetchJson<SettingsResponse>("/api/console/settings", { method: "PATCH", body: JSON.stringify(input) });
    if (data.persisted === false) throw new Error("Couldn't save this setting.");
    return data;
  }, onSuccess: data => { queryClient.setQueryData(settings.queryKey, data); setMessage("Saved."); } });
  const reset = useMutation({ mutationFn: async () => {
    const keys = sharedSettingKeys(resetKeys?.length ? resetKeys : allDefinitions.map(def => def.key));
    if (!keys.length) throw new Error("No shared agent preferences are available to reset.");
    const data = await webFetchJson<SettingsResponse>("/api/console/settings/reset", jsonBody({ keys }));
    if (data.persisted === false) throw new Error("Couldn't reset settings.");
    return data;
  }, onSuccess: data => { queryClient.setQueryData(settings.queryKey, data); setResetKeys(null); setMessage("Reset to defaults."); } });
  const allDefinitions = settings.data?.definitions.filter(def => visibleWebSetting(def) && !presentationOnly) ?? [];
  useEffect(() => {
    if (!settings.isSuccess || presentationOnly) return;
    let anchor = location.hash.slice(1);
    try { anchor = decodeURIComponent(anchor); } catch { return; }
    const id = anchor.startsWith("setting-") || anchor.startsWith("settings-") ? anchor : `settings-${category}`;
    const frame = requestAnimationFrame(() => { const target = document.getElementById(id); target?.scrollIntoView({ block: "start" }); if (anchor.startsWith("setting-")) target?.focus({ preventScroll: true }); });
    return () => cancelAnimationFrame(frame);
  }, [category, location.hash, settings.isSuccess, presentationOnly]);
  return <div className="space-y-4">
    <QueryState pending={settings.isPending} error={settings.error} retry={settings.refetch} />
    <Feedback error={save.error ?? (resetKeys === null ? reset.error : null)} message={message} />
    <Dialog open={resetKeys !== null} onOpenChange={open => { if (!open && !reset.isPending) setResetKeys(null); }}><DialogContent showCloseButton={!reset.isPending} onCloseAutoFocus={event => { event.preventDefault(); resetTriggerRef.current?.focus(); }}><DialogHeader><DialogTitle>Reset {resetKeys?.length ? settings.data?.definitions.find(def => def.key === resetKeys[0])?.label ?? "this setting" : "shared preferences"}?</DialogTitle><DialogDescription>Restore shared agent preferences. Appearance is unchanged.</DialogDescription></DialogHeader><Feedback error={reset.error} /><DialogFooter><Button variant="ghost" onClick={() => setResetKeys(null)} disabled={reset.isPending}>Cancel</Button><SubmitButton variant="destructive" pending={reset.isPending} onClick={() => reset.mutate()}>Reset</SubmitButton></DialogFooter></DialogContent></Dialog>
    {settingsCategories.map(section => {
      const definitions = allDefinitions.filter(def => categoryFor(def) === section.id);
      if (!definitions.length && section.id !== "appearance" && (section.id !== "agents" || presentationOnly)) return null;
      return <SettingsSection key={section.id} id={`settings-${section.id}`} title={section.label} description={section.id === "appearance" ? "Saved in this browser." : undefined} quiet={presentationOnly}>
        {section.id === "appearance" && <>
          <SettingsRow id="setting-web-theme" label="Theme"><Select aria-label="Web appearance" className="w-full" value={appearance} onValueChange={value => { if (value === "light" || value === "dark") setAppearance(value); }} options={[{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }]} /></SettingsRow>
        </>}
        {definitions.map(def => <SettingRow key={def.key} definition={def} value={settings.data!.settings[def.key]} scope={scope} pending={save.isPending || reset.isPending} apply={(key, value, layer) => save.mutate({ key, value, scope: layer })} />)}
        {!presentationOnly && section.id === "agents" && <ExecutionControl />}
      </SettingsSection>;
    })}
    {!presentationOnly && settings.data && <SettingsSection id="settings-reset" title="Reset settings"><SettingsRow id="setting-reset" label="Shared preferences" description="Appearance is unchanged."><Button variant="outline" disabled={save.isPending || reset.isPending || !allDefinitions.length} onClick={() => openReset([])}>Reset</Button></SettingsRow></SettingsSection>}
  </div>;
}
