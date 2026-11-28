import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ProviderGlyph } from "@/components/provider-glyph";
import { useBackendApi } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/page-header";

/* THE MODELS PAGE — one place: pick provider, enter key/base URL, live-fetch
   models, pick model + effort, see the REAL request JSON, save, test ping.
   The JSON editor at the bottom is authoritative at save: the engine merges
   it verbatim into every call. */

interface ProviderSpec {
  key: string; label: string; baseUrl: string; keyEnv: string;
  defaultModel: string; configured: boolean; local: boolean;
  schema: { key: string; label: string; kind: string; options?: string[]; placeholder?: string; default?: string }[];
  apiStyles: string[];
}
interface CatalogResponse { providers: ProviderSpec[] }

const POPULAR = ["openai", "anthropic", "zai", "openrouter", "google", "xai", "groq", "mistral", "deepseek", "together", "fireworks", "qwen", "moonshot"];

function hostOf(url: string): string {
  try { return new URL(url).hostname; } catch { return ""; }
}
function iconUrl(p: { icon?: string; baseUrl?: string; label?: string; key?: string }): string {
  if (p.icon) return p.icon;
  const h = hostOf(p.baseUrl ?? "");
  return h ? `https://www.google.com/s2/favicons?domain=${h}&sz=64` : "";
}

export function ModelsPage() {
  const { webFetchJson } = useBackendApi();
  const [selected, setSelected] = useState<string>("zai");
  const [apiKey, setApiKey] = useState("");
  const [baseUrlEdit, setBaseUrlEdit] = useState<string | null>(null);
  const [apiStyle, setApiStyle] = useState<string>("");
  const [extras, setExtras] = useState<Record<string, string>>({});
  const [connectMsg, setConnectMsg] = useState("");
  const [fetched, setFetched] = useState<{ models: string[]; note?: string } | null>(null);
  const [fetching, setFetching] = useState(false);
  const [model, setModel] = useState("");
  const [providerSearch, setProviderSearch] = useState("");
  const [modelSearch, setModelSearch] = useState("");
  const [effort, setEffort] = useState("medium");
  const [overrideJson, setOverrideJson] = useState<string | null>(null);
  const [jsonDirty, setJsonDirty] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");
  const [testResult, setTestResult] = useState<string>("");
  const [health, setHealth] = useState<Record<string, { ok: boolean | null; status?: string; error?: string }>>(() => {
    try { return JSON.parse(window.localStorage.getItem("suijin-provider-health") || "{}"); } catch { return {}; }
  });
  const [verifying, setVerifying] = useState(false);
  const verifyAll = async () => {
    setVerifying(true);
    const next = { ...health };
    for (const pr of (catalog.data?.providers ?? []).filter(p => p.configured)) {
      try {
        const r = await webFetchJson<{ ok: boolean; status?: number | null; error?: string }>(`/api/console/providers/${pr.key}/verify`, { method: "POST", body: "{}" });
        next[pr.key] = { ok: r.ok, status: r.status == null ? "network" : String(r.status), error: r.error?.slice(0, 200) };
      } catch (e) {
        next[pr.key] = { ok: false, status: "error", error: String(e) };
      }
      setHealth({ ...next });
    }
    try { window.localStorage.setItem("suijin-provider-health", JSON.stringify(next)); } catch { /* ignore */ }
    setVerifying(false);
  };

  const catalog = useQuery<CatalogResponse>({ queryKey: ["provider-catalog"], queryFn: () => webFetchJson("/api/console/provider-catalog"), staleTime: Infinity });
  const providersDev = useQuery<Record<string, unknown>>({ queryKey: ["models-dev"], queryFn: () => webFetchJson<Record<string, unknown>>("/api/console/models-dev"), staleTime: 600_000, retry: 1 });
  const status = useQuery<{ providers: { id: string; configured: boolean }[] }>({ queryKey: ["providers-status"], queryFn: () => webFetchJson<{ providers: { id: string; configured: boolean }[] }>("/api/console/providers"), refetchInterval: 15000 });

  const spec = useMemo(() => (catalog.data?.providers ?? []).find(p => p.key === selected), [catalog.data, selected]);
  // switching provider invalidates the old model/effort/fetch state and
  // pre-fills the provider's default model (the effort picker needs a model)
  useEffect(() => {
    setFetched(null);
    setEffort("medium");
    setModel(spec?.defaultModel ?? "");
  }, [selected, spec]);
  const devEntry = useMemo(() => {
    const d = providersDev.data as Record<string, { api?: string; env?: string[]; models?: Record<string, { limit?: { context?: number }; reasoning?: boolean | { options?: unknown[] }; cost?: unknown }> }> | undefined;
    if (!d) return undefined;
    // models.dev keys don't always match our registry keys — try direct, then alias
    const alias: Record<string, string> = { "z-ai": "zai", "xai": "xai", "google": "google" };
    return d[selected] ?? d[alias[selected] ?? ""];
  }, [providersDev.data, selected]);

  // PRECEDENCE (field 2026-11-17: models.dev's paas URL overrode the
  // operator's coding-plan default — the endpoint select must win when the
  // provider has one; otherwise models.dev api, then the registry spec)
  const effectiveBaseUrl = baseUrlEdit
    ?? (selected === "zai"
        ? ((extras.endpoint_style ?? "coding plan") === "paas" ? "https://api.z.ai/api/paas/v4" : (spec?.baseUrl ?? devEntry?.api))
        : (devEntry?.api ?? spec?.baseUrl ?? ""));
  const isCustom = selected === "custom";
  const urlEditable = isCustom || selected === "cloudflare" || Boolean(spec && spec.local) || (spec && spec.schema.length === 0 && !devEntry?.api);
  const modelMeta = useMemo(() => {
    const entries = devEntry?.models ?? {};
    const hit = entries[model];
    return hit ?? undefined;
  }, [devEntry, model]);
  const supportsReasoning = Boolean(modelMeta?.reasoning);
  // ACCURATE EFFORT (field 2026-11-17): models.dev declares the exact
  // effort values per model ("effort::low,high,max", "xhigh,max",
  // "none,low,medium,high,xhigh,max"…). Render THOSE — never invent a
  // generic low/medium/high.
  const effortOptions = useMemo(() => {
    const ro = (modelMeta as { reasoning_options?: { type?: string; values?: string[] }[] } | undefined)?.reasoning_options;
    if (!Array.isArray(ro)) return [];
    for (const opt of ro) {
      if (opt.type === "effort" && Array.isArray(opt.values) && opt.values.length) return opt.values;
    }
    if (ro.some(opt => opt.type === "toggle")) return ["on", "off"];
    return [];
  }, [modelMeta]);
  useEffect(() => { if (effortOptions.length && !effortOptions.includes(effort)) setEffort(effortOptions.includes("medium") ? "medium" : effortOptions.includes("high") ? "high" : effortOptions[0]); }, [effortOptions]);

  const buildOverride = (): Record<string, unknown> => {
    const base = {
      provider: selected,
      label: spec?.label ?? selected,
      baseUrl: effectiveBaseUrl,
      api: (apiStyle || spec?.apiStyles?.[0] || "chat"),
      auth: { type: (spec?.local ? "none" : "bearer"), keyEnv: spec?.keyEnv || `CUSTOM_${selected.toUpperCase()}_API_KEY`, extraHeaders: {} as Record<string, string> },
      paths: { chat: "/chat/completions", models: selected === "ollama" ? "/api/tags" : "/models" },
      model,
      effort,
    };
    const q: Record<string, string> = {};
    const ex = extras || {};
    // provider-specific extras that live in well-known places
    if (selected === "azure" && ex.api_version) q["api-version"] = ex.api_version;
    if (selected === "cloudflare" && ex.account_id) {
      base.baseUrl = `https://api.cloudflare.com/client/v4/accounts/${ex.account_id}/ai/v1`;
    }
    const out: Record<string, unknown> = { ...base };
    if (Object.keys(q).length) out.query = q;
    if (Object.keys(ex).length) out.extras = ex;
    return out;
  };

  const currentJson = useMemo(() => {
    if (overrideJson !== null) return overrideJson;
    try { return JSON.stringify(buildOverride(), null, 2); } catch { return "{}"; }
  }, [overrideJson, selected, baseUrlEdit, apiStyle, extras, model, effort, devEntry, spec, apiKey]);

  const connect = async () => {
    setConnectMsg("");
    try {
      const r = await webFetchJson<{ message?: string; error?: string }>("/api/console/connections", {
        method: "POST",
        body: JSON.stringify({ providerId: selected === "custom" ? "custom" : selected, action: apiKey ? "connect" : "clear", apiKey, baseUrl: effectiveBaseUrl, extras }),
      });
      setConnectMsg(r.message ?? "saved");
    } catch (e) { setConnectMsg(e instanceof Error ? e.message : "connect failed"); }
  };

  const fetchModels = async () => {
    setFetching(true); setFetched(null);
    try {
      const r = await webFetchJson<{ models: string[]; note?: string }>(`/api/console/providers/${encodeURIComponent(selected)}/models`);
      setFetched({ models: r.models ?? [], note: r.note });
      if (r.models?.length && !model) setModel(r.models.includes(spec?.defaultModel ?? "") ? (spec?.defaultModel as string) : r.models[0]);
    } catch (e) { setFetched({ models: [], note: e instanceof Error ? e.message : "fetch failed" }); }
    finally { setFetching(false); }
  };

  const saveOverride = async () => {
    setSaveMsg("");
    try {
      const parsed = JSON.parse(currentJson);
      await webFetchJson(`/api/console/provider-override/${encodeURIComponent(selected)}`, {
        method: "PUT", body: JSON.stringify({ override: parsed }),
      });
      // persist the runtime pick — reflects everywhere
      if (model) {
        await webFetchJson("/api/console/runtime", {
          method: "PATCH",
          body: JSON.stringify({ providerId: selected === "custom" ? "custom" : selected, model, effort }),
        });
      }
      setJsonDirty(false); setOverrideJson(null);
      setSaveMsg(model ? `saved — ${model} on ${selected} is live everywhere` : "saved");
    } catch (e) { setSaveMsg(e instanceof Error ? `invalid: ${e.message}` : "save failed"); }
  };

  const testPing = async () => {
    setTestResult("pinging…");
    try {
      let parsed: unknown = undefined;
      try { parsed = JSON.parse(currentJson); } catch { setTestResult("JSON doesn't parse — fix it first"); return; }
      const r = await webFetchJson<{ ok: boolean; reply?: string; error?: string; status?: number; url?: string }>(
        `/api/console/provider-override/${encodeURIComponent(selected)}/test`,
        { method: "POST", body: JSON.stringify({ override: parsed, model }) });
      setTestResult(r.ok ? `✓ ${r.status} — replied: ${r.reply}` : `✗ ${r.status ?? ""} ${r.error}`);
    } catch (e) { setTestResult(`✗ ${e instanceof Error ? e.message : "test failed"}`); }
  };

  const statusLine = (status.data?.providers ?? []).find(pr => pr.id === selected || pr.id === "z-ai" && selected === "zai");
  const filteredCatalog = useMemo(() => {
    const all = catalog.data?.providers ?? [];
    const q = providerSearch.toLowerCase();
    return all.filter(p => !q || p.label.toLowerCase().includes(q) || p.key.includes(q));
  }, [catalog.data, modelSearch]);
  const configuredKeys = new Set((status.data?.providers ?? []).filter(p => p.configured).map(p => p.id === "z-ai" ? "zai" : p.id));

  return <div className="mx-auto w-full max-w-3xl space-y-5 p-6">
    <div className="flex justify-end"><Button variant="ghost" size="sm" onClick={() => { window.history.length > 1 ? window.history.back() : window.location.assign("/chats"); }}>← Back</Button></div>
    <PageHeader title="Models" summary="Pick the model that runs your engagements. Keys stay in your local .env — they never leave this machine." />

    {/* provider selector */}
    <section className="space-y-2">
      <label className="text-xs font-medium text-muted-foreground">Provider</label>
      <Input placeholder="Search providers…" value={providerSearch} onChange={e => { setProviderSearch(e.target.value); }} />
      <div className="max-h-56 space-y-0.5 overflow-y-auto rounded-lg border border-border p-1">
        <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-muted" data-ui-selected={selected === "custom" || undefined} onClick={() => { setSelected("custom"); setBaseUrlEdit(""); setFetched(null); setModel(""); }}>
          <span className="size-4 rounded border border-border text-center text-[10px] leading-4">＋</span> Custom (OpenAI-compatible)
        </button>
        {configuredKeys.size > 0 && <div className="px-2 pt-1 text-[10px] uppercase tracking-wide text-muted-foreground">Configured</div>}
        {filteredCatalog.filter(p => configuredKeys.has(p.key)).map(p =>
          <ProviderRow key={p.key} p={p} selected={selected === p.key} onClick={() => { setSelected(p.key); setBaseUrlEdit(null); setFetched(null); setModel(""); setExtras({}); }} />)}
        <div className="px-2 pt-1 text-[10px] uppercase tracking-wide text-muted-foreground">Popular</div>
        {filteredCatalog.filter(p => POPULAR.includes(p.key) && !configuredKeys.has(p.key)).map(p =>
          <ProviderRow key={p.key} p={p} selected={selected === p.key} onClick={() => { setSelected(p.key); setBaseUrlEdit(null); setFetched(null); setModel(""); setExtras({}); }} />)}
        <div className="px-2 pt-1 text-[10px] uppercase tracking-wide text-muted-foreground">All providers</div>
        {filteredCatalog.filter(p => !POPULAR.includes(p.key) && !configuredKeys.has(p.key)).map(p =>
          <ProviderRow key={p.key} p={p} selected={selected === p.key} onClick={() => { setSelected(p.key); setBaseUrlEdit(null); setFetched(null); setModel(""); setExtras({}); }} />)}
      </div>
    </section>

    {/* setup form — schema driven */}
    {spec && <section className="space-y-3 rounded-xl border border-border p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">{spec.label}</h2>
        {statusLine && <span className="text-xs text-muted-foreground">{statusLine.configured ? "key saved" : "not configured"}</span>}
      </div>

      <label className="block space-y-1">
        <span className="text-xs text-muted-foreground">API key {!spec.local && <span className="opacity-60">({spec.keyEnv || "env"})</span>}</span>
        <Input type="password" value={apiKey} disabled={spec.local} onChange={e => setApiKey(e.target.value)} placeholder={spec.local ? "not needed for local engines" : "paste key — stored in local .env only"} />
      </label>

      <label className="block space-y-1">
        <span className="text-xs text-muted-foreground">Base URL {urlEditable ? "" : <span className="opacity-60">(default)</span>}</span>
        <Input value={effectiveBaseUrl} disabled={!urlEditable} onChange={e => setBaseUrlEdit(e.target.value)} placeholder={spec.baseUrl || "https://…"} className={urlEditable ? "" : "opacity-60"} />
      </label>

      {(spec.apiStyles?.length ?? 1) > 1 && <label className="block space-y-1">
        <span className="text-xs text-muted-foreground">API style</span>
        <select className="h-9 w-full rounded-lg border border-border bg-background px-2 text-sm" value={apiStyle || spec.apiStyles[0]} onChange={e => setApiStyle(e.target.value)}>
          {spec.apiStyles.map(s => <option key={s} value={s}>{s === "chat" ? "Chat Completions" : s === "responses" ? "Responses" : s}</option>)}
        </select>
      </label>}

      {(spec.schema?.length ?? 0) > 0 && <div className="space-y-2">
        <div className="text-xs text-muted-foreground">{spec.label} specifics</div>
        {spec.schema.map(field => <label key={field.key} className="block space-y-1">
          <span className="text-xs text-muted-foreground">{field.label}</span>
          {field.kind === "select"
            ? <select className="h-9 w-full rounded-lg border border-border bg-background px-2 text-sm" value={extras[field.key] ?? field.default ?? ""} onChange={e => setExtras(prev => ({ ...prev, [field.key]: e.target.value }))}>
                {(field.options ?? []).map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            : <Input type={field.kind === "password" ? "password" : "text"} value={extras[field.key] ?? ""} placeholder={field.placeholder ?? ""} onChange={e => setExtras(prev => ({ ...prev, [field.key]: e.target.value }))} />}
        </label>)}
      </div>}

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={() => void connect()} disabled={spec.local && !urlEditable}>Connect</Button>
        <Button size="sm" variant="outline" onClick={() => void fetchModels()} disabled={fetching}>{fetching ? "fetching…" : "Fetch models"}</Button>
        {connectMsg && <span className="text-xs text-muted-foreground">{connectMsg}</span>}
      </div>
      {!spec.local && !spec.configured && !apiKey && <p className="text-xs text-amber-500">Enter your API key and connect first — fetching needs it to authenticate.</p>}
      {fetched && <p className="text-xs text-muted-foreground">{fetched.models.length ? `${fetched.models.length} models served` : `no list served${fetched.note ? ` — ${fetched.note}` : " — type the model id"}`}</p>}
    </section>}

    {/* model pick — ONE box: type freely, or pick from the live list */}
    <section className="space-y-2">
      <label className="text-xs font-medium text-muted-foreground">Model</label>
      <Input value={model} onChange={e => { setModel(e.target.value); }} placeholder="glm-5.3 — type an id, or fetch and pick below" />
      {fetching && <p className="text-xs text-muted-foreground">fetching the provider's model list…</p>}
      {fetched?.models.length ? <div className="max-h-48 space-y-0.5 overflow-y-auto rounded-lg border border-border p-1">
        {fetched.models.filter(mid => !modelSearch || mid.toLowerCase().includes(modelSearch.toLowerCase())).map(mid => {
          const meta = devEntry?.models?.[mid];
          return <button key={mid} className="flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-sm hover:bg-muted" data-ui-selected={model === mid || undefined} onClick={() => { setModel(mid); setModelSearch(""); }}>
            <span className="truncate">{mid}</span>
            <span className="shrink-0 text-[10px] text-muted-foreground">{meta?.limit?.context ? `${Math.round((meta.limit.context) / 1000)}K` : meta?.reasoning ? "reasoning" : ""}</span>
          </button>;
        })}
      </div> : null}
      {model && !supportsReasoning && <p className="text-xs text-muted-foreground">{modelMeta ? "this model is not reasoning-capable — no effort setting" : ""}</p>}
      {model && supportsReasoning && effortOptions.length > 0 && <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">Effort</span>
        <select className="h-8 rounded-lg border border-border bg-background px-2 text-xs" value={effort} onChange={e => setEffort(e.target.value)}>
          {effortOptions.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
        <span className="text-[10px] text-muted-foreground">{modelMeta?.limit?.context ? `context ${modelMeta.limit.context.toLocaleString()} tokens` : ""}</span>
      </div>}
    </section>

    {/* THE REAL JSON — authoritative at save */}
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-muted-foreground">Request config — the real JSON the engine runs. Edit freely.</label>
        {jsonDirty && <span className="text-[10px] text-amber-500">modified</span>}
      </div>
      <textarea spellCheck={false} className="max-h-96 min-h-48 w-full resize-y overflow-auto rounded-lg border border-border bg-muted/30 p-3 font-mono text-xs leading-5 outline-none focus-visible:outline-2 focus-visible:outline-ring" value={currentJson} onChange={e => { setOverrideJson(e.target.value); setJsonDirty(true); }} />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => void saveOverride()}>Save</Button>
        <Button size="sm" variant="outline" onClick={() => void testPing()}>Test ping</Button>
        {jsonDirty && <Button size="sm" variant="ghost" onClick={() => { setOverrideJson(null); setJsonDirty(false); }}>Reset to form</Button>}
        {saveMsg && <span className="text-xs text-emerald-500">{saveMsg}</span>}
        {testResult && <span className="text-xs text-muted-foreground">{testResult}</span>}
      </div>
    </section>

    {/* diagnostics — honest: key saved ≠ verified working */}
    <section className="space-y-1 rounded-xl border border-border p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Diagnostics</h3>
        <Button size="sm" variant="ghost" onClick={() => void verifyAll()} disabled={verifying}>{verifying ? "verifying…" : "Verify all"}</Button>
      </div>
      <p className="text-xs text-muted-foreground">"connected" means a key is saved. Verify pings each provider once (uses 1 token).</p>
      {(catalog.data?.providers ?? []).slice(0, 15).map(pr => {
        const h = health[pr.key];
        const state = !pr.configured ? "idle" : h ? (h.ok ? "ok" : "fail") : "untested";
        const badge = state === "ok" ? "verified"
          : state === "fail" ? `failing${h?.status ? ` (${h.status})` : ""}`
          : state === "untested" ? "key saved · untested"
          : "not configured";
        const color = state === "ok" ? "text-emerald-500"
          : state === "fail" ? "text-red-500"
          : state === "untested" ? "text-amber-500"
          : "text-muted-foreground";
        return <div key={pr.key} className="flex items-center justify-between gap-2 text-xs">
          <span className="flex min-w-0 items-center gap-2"><ProviderGlyph src={iconUrl(pr)} label={pr.label} /><span className="truncate">{pr.label}</span></span>
          <span className={color} title={h?.error ?? undefined}>{badge}</span>
        </div>;
      })}
      {providersDev.isError && <p className="text-xs text-amber-500">models.dev unreachable — using registry defaults</p>}
    </section>
  </div>;
}

function ProviderRow({ p, selected, onClick }: { p: ProviderSpec; selected: boolean; onClick: () => void }) {
  const src = iconUrl(p);
  return <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-muted" data-ui-selected={selected || undefined} onClick={onClick}>
    <ProviderGlyph src={src} label={p.label} />
    <span className="min-w-0 flex-1 truncate">{p.label}</span>
    {p.configured && <span className="text-[10px] text-amber-500">key saved</span>}
  </button>;
}
