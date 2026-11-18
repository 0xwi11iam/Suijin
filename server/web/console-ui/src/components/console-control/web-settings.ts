/**
 * Suijin settings — which preferences the web console manages, their
 * grouping and labels. Values live in config.json (the adapter reads and
 * writes them); the pills/pickers are the shell's own components.
 */
export type SettingsCategory = "general" | "agents" | "conversation" | "appearance" | "privacy";
type WebSetting = { category: SettingsCategory; label: string; description: string };
export const settingsCategories = [
  { id: "agents", label: "Swarm & agents" },
  { id: "conversation", label: "Engagements" },
  { id: "general", label: "Guards & ledger" },
  { id: "appearance", label: "Appearance" },
] as const satisfies readonly SettingsCategory[];
const sharedSettings = {
  swarm_autopilot: { category: "agents", label: "Autopilot", description: "Coordinator requeues intents and summons agents on its own." },
  swarm_turn_cap: { category: "agents", label: "Turn cap", description: "Per-agent turn budget for engagements. none = unlimited." },
  complexity: { category: "agents", label: "Complexity", description: "Gates advanced swarm verbs." },
  memory_preflight_guard: { category: "agents", label: "Memory guard", description: "Refuse spawn plans that exceed the RAM budget." },
  sandbox_pool_size: { category: "agents", label: "Warm pool", description: "Idle boxes kept ready for instant attach." },
  transport_scope_guard: { category: "general", label: "Scope guard", description: "Drop requests outside the engagement scope at the transport layer." },
  transport_method_guard: { category: "general", label: "Method guard", description: "Restrict unsafe methods at the transport layer." },
  transport_rate_limit: { category: "general", label: "Rate limit", description: "Requests per second ceiling." },
  ledger_retest_days: { category: "general", label: "Retest window", description: "Stale verdicts invite retesting after this many days." },
  routing_worker_model: { category: "conversation", label: "Engagement model", description: "Volume model for engagements (worker tier)." },
  model_1: { category: "conversation", label: "M1 · Leader model", description: "Structured mode: the leader's brain. Required." },
  model_2: { category: "conversation", label: "M2 · Supervisor model", description: "Structured mode: supervisors. Defaults to M1." },
  model_3: { category: "conversation", label: "M3 · Worker model", description: "Structured mode: workers. Cascade m3→m2→m1." },
} as const satisfies Record<string, WebSetting>;
const terminalSettings = new Set([
  "showStatusBar", "showComposerHints", "composerSuggestions", "mouseSupport", "showLogo", "showRuntimeNotices", "showTurnSummary", "showSubagents", "showTimestamps", "showObjective", "showScope", "density", "composerStyle", "transcriptStyle", "roleLabelStyle", "toolCardStyle", "richToolCards", "transcriptDetail", "theme", "allowDevSourceUpdates", "autoEvolveFinderLenses", "autoPromoteFinderLenses", "showTokenUsage", "showCost", "showContextMeter", "modelDisplay", "elapsedTimer", "logoAnimation", "symbolPreset", "rosterSort", "leaderKey",
]);
const browserSettings = new Set(["web-theme"]);
export function settingSurface(key: string): "shared" | "browser" | "terminal" | "unsupported" {
  if (Object.hasOwn(sharedSettings, key)) return "shared";
  if (browserSettings.has(key)) return "browser";
  if (terminalSettings.has(key)) return "terminal";
  return "unsupported";
}
export function visibleWebSetting(definition: { key: string }): boolean { return settingSurface(definition.key) === "shared"; }
/** Reset payloads are explicit, deduplicated, and cannot include terminal or browser preferences. */
export function sharedSettingKeys(keys: Iterable<string>): string[] { return [...new Set(keys)].filter(key => settingSurface(key) === "shared"); }
export function webSettingDetails(key: string): WebSetting | undefined { return Object.hasOwn(sharedSettings, key) ? sharedSettings[key as keyof typeof sharedSettings] : undefined; }
export function categoryFor(definition: { key: string }): SettingsCategory { return webSettingDetails(definition.key)?.category ?? "agents"; }
export function settingLabel(definition: { key: string; label: string }): string { return webSettingDetails(definition.key)?.label ?? definition.label; }
export function settingChoiceLabel(key: string, choice: string): string {
  const labels: Record<string, Record<string, string>> = {
    swarm_turn_cap: { "0": "Unlimited" },
    sandbox_pool_size: { "0": "Off", "2": "2 boxes", "4": "4 boxes", "8": "8 boxes" },
    transport_rate_limit: { off: "Off" },
    ledger_retest_days: { "30": "30 days", "90": "90 days", "180": "180 days" },
    // model_* options are dynamic (provider:model) from /api/console/available-models — never hardcoded
    complexity: { basic: "Basic", advanced: "Advanced" },
  };
  return labels[key]?.[choice] ?? choice.charAt(0).toUpperCase() + choice.slice(1);
}
