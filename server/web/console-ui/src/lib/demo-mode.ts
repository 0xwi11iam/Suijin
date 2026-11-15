/**
 * SUIJIN DEMO MODE — the whole frontend renders with fake data, no engine.
 *
 * Intercepted at the single transport choke point (`localControlFetch` in
 * api.ts). Every `/api/...` GET here returns a plausible Suijin payload;
 * non-GETs return `{ok:true}`. Delete this file + its import when the real
 * Suijin backend lands.
 *
 * NOTE: demo targets/findings use example.com only (engagement
 * confidentiality applies to demo artifacts too).
 */

const CAPABILITIES = [
  "sessions", "workflows", "workflow-engine", "schedules", "approvals", "events", "workspaces",
  "artifacts", "model-connections", "provider-relay", "process-controls", "operator-services", "learning",
];

const PLATFORM = { os: "darwin", pathStyle: "posix" };

export const DEMO_DESCRIPTOR = {
  id: "local",
  name: "suijin",
  transport: "local",
  status: "connected",
  protocolVersion: 1,
  capabilities: CAPABILITIES,
  platform: PLATFORM,
};

const DEMO_HANDSHAKE = {
  protocolVersion: 1,
  engineId: "local",
  capabilities: CAPABILITIES,
  platform: PLATFORM,
  serverInstanceId: "suijin-demo",
};

const now = Date.now();
const iso = (ms: number) => new Date(ms).toISOString();

const scanSummary = (c: number, h: number, m: number, l: number) => ({
  totalFindings: c + h + m + l, totalAttacks: (c + h + m + l) * 7,
  critical: c, high: h, medium: m, low: l, info: 2,
});

const demoScan = (id: string, target: string, status: string, startedMs: number) => ({
  id, target, depth: "default", runtime: "api", mode: "hunt", status,
  startedAt: iso(startedMs), completedAt: status === "running" ? null : iso(startedMs + 5_400_000),
  durationMs: status === "running" ? null : 5_400_000,
  summary: scanSummary(1, 1, 2, 3),
});

const workflow = {
  status: "in_progress", persistedStatus: "in_progress", recommendedStatus: "in_progress",
  phase: "in_progress", reviewGate: "none", reviewReason: null, assignee: "agent-1321-54a4",
  updatedAt: iso(now - 3_600_000), consensus: "pending", activeAgentRoles: ["recon", "exploit"],
  evidenceSignal: "strong",
  verdictCounts: { truePositive: 1, falsePositive: 0, unsure: 1, total: 2 },
};

const finding = {
  id: "find-9001", scanId: "hunt-114",
  title: "role param auth bypass on /api/v2/users",
  description: "Swapping role=admin on the profile endpoint returns other users' records without an authorization check.",
  severity: "high", category: "broken_access_control", status: "reported",
  fingerprint: "fp-auth-bypass-9001", triageStatus: "accepted",
  workflowStatus: "in_progress", workflowAssignee: "agent-1321-54a4", workflowUpdatedAt: iso(now - 3_600_000),
  timestamp: now - 7_200_000, score: 8.6, confidence: 0.92, cvssScore: 8.6,
  cvssVector: "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:N",
  evidenceRequest: "GET /api/v2/users/42?role=admin",
  evidenceResponse: "200 OK — {email:\"victim@example\", role:\"user\"}",
};

const demoGroup = {
  fingerprint: "fp-auth-bypass-9001", latest: finding, count: 3, scanCount: 2,
  sourceScanIds: ["hunt-114", "hunt-110"], workflow,
};

const demoWorker = (id: string, label: string, status: string, lane: string | null) => ({
  id, role: "orchestrator", status, label,
  currentCaseId: "case-01", currentCaseTarget: "https://target.example",
  currentWorkItemId: "wi-11", currentWorkItemTitle: lane, currentWorkItemKind: "hypothesis",
  currentScanId: "hunt-114", pid: 4211, host: null, lastError: null,
  heartbeatAt: iso(now - 3_000), startedAt: iso(now - 4_000_000), updatedAt: iso(now - 3_000),
  isActive: status !== "stopped",
});

const DEMO_SESSION = {
  id: "chat-demo-01", target: "", role: "root", autonomyMode: "standard",
  scopeConfigured: false, localScopeConfigured: false, status: "idle",
  createdAt: iso(now), updatedAt: iso(now), title: "new swarm", messageCount: 0,
};

const DEMO_SNAPSHOT = {
  session: DEMO_SESSION, title: "new swarm", cursor: 0,
  messages: [], events: [], pendingDecisions: [], workers: [], queuedMessages: [],
  runtime: null, scope: null,
  scopeEnforcement: { enabled: false, projectPath: "suijin" },
  workspacePath: "suijin workspace",
  usage: { inputTokens: 0, outputTokens: 0 },
};

const demoProviders = [
  { id: "z-ai", label: "Z.ai", methods: ["api-key"], configured: true, source: "env", connectionIdentity: "demo-zai", diagnostics: [], accounts: [{ accountId: "demo", kind: "api-key", active: true, label: "suijin plan", status: "active" }], auth: { phase: "connected", message: "connected", lines: [], verificationUrl: null, userCode: null, available: true }, configuration: { model: "glm-5.3-flash" } },
  { id: "anthropic", label: "Anthropic", methods: ["api-key", "oauth"], configured: false, source: null, diagnostics: [], accounts: [], auth: { phase: "unconfigured", message: "no credential", lines: [], verificationUrl: null, userCode: null, available: true } },
  { id: "openai", label: "OpenAI", methods: ["api-key", "oauth"], configured: false, source: null, diagnostics: [], accounts: [], auth: { phase: "unconfigured", message: "no credential", lines: [], verificationUrl: null, userCode: null, available: true } },
  { id: "google", label: "Google", methods: ["oauth"], configured: false, source: null, diagnostics: [], accounts: [], auth: { phase: "unconfigured", message: "no credential", lines: [], verificationUrl: null, userCode: null, available: true } },
  { id: "openrouter", label: "OpenRouter", methods: ["api-key"], configured: false, source: null, diagnostics: [], accounts: [], auth: { phase: "unconfigured", message: "no credential", lines: [], verificationUrl: null, userCode: null, available: true } },
  { id: "ollama", label: "Ollama", methods: ["api-key"], configured: false, source: null, diagnostics: [], accounts: [], auth: { phase: "unconfigured", message: "not running", lines: [], verificationUrl: null, userCode: null, available: true } },
];

const demoModels = {
  models: [
    { id: "glm-5.3-flash", provider: "z-ai", price: "$0.10/M in", contextWindowTokens: 200_000, source: "account" },
    { id: "glm-4.7-air", provider: "z-ai", price: "$0.05/M in", contextWindowTokens: 128_000, source: "account" },
    { id: "claude-sonnet-4.6", provider: "anthropic", price: "$3/M in", contextWindowTokens: 200_000, source: "public-catalog" },
    { id: "gpt-5.2", provider: "openai", price: "$2/M in", contextWindowTokens: 128_000, source: "public-catalog" },
    { id: "kimi-k2", provider: "kimi", price: "$0.60/M in", contextWindowTokens: 256_000, source: "public-catalog" },
    { id: "qwen3-max", provider: "qwen", price: "$1.20/M in", contextWindowTokens: 128_000, source: "public-catalog" },
  ],
  providerId: "z-ai",
  diagnostics: [],
  roles: ["planner", "worker", "escalate"],
};

const demoSkills = {
  skills: [
    { id: "skill-recon", name: "recon-sweep", description: "subfinder → httpx → nuclei surface pass", scope: "builtin", revision: "r3", updatedAt: iso(now - 86_400_000) },
    { id: "skill-exploit", name: "exploit-reproduce", description: "minimal PoC + control test before status advances", scope: "builtin", revision: "r5", updatedAt: iso(now - 172_800_000) },
    { id: "skill-fuzz", name: "transport-fuzz", description: "scoped param fuzzing through the guarded session", scope: "builtin", revision: "r2", updatedAt: iso(now - 259_200_000) },
    { id: "skill-jwt", name: "jwt-confusion", description: "alg-none / kid-injection / weak-secret passes", scope: "workspace", revision: "r1", updatedAt: iso(now - 345_600_000) },
    { id: "skill-auth", name: "auth-session-walk", description: "jar-aware crawl behind the login wall", scope: "workspace", revision: "r4", updatedAt: iso(now - 432_000_000) },
  ],
  mounts: [],
  diagnostics: [],
  workspacePath: "suijin workspace",
};

const demoPlugins = {
  executionProfile: "local",
  registry: { url: "suijin-local", available: false, error: null },
  items: [
    { id: "plugin-transport-guards", kind: "plugin", name: "transport guards", version: "1.4.0", description: "scope/method/rate enforced at the HTTP session layer", capabilities: ["transport"], signature: "verified", state: "active", loaded: true, error: null },
    { id: "plugin-mesh-bridge", kind: "plugin", name: "mesh bridge", version: "1.2.0", description: "gc.log ↔ mesh chat without echo loops", capabilities: ["mesh"], signature: "verified", state: "active", loaded: true, error: null },
    { id: "plugin-knowledge-graph", kind: "plugin", name: "knowledge graph", version: "0.9.1", description: "workspace-first intel graph", capabilities: ["memory"], signature: "verified", state: "installed", loaded: false, error: null },
    { id: "plugin-obsidian-sync", kind: "plugin", name: "obsidian decision log", version: "0.3.0", description: "push decisions to the vault", capabilities: ["docs"], signature: "unverified", state: "available", loaded: false, error: null },
  ],
  deferred: [],
  host: { loadedPluginIds: ["plugin-transport-guards", "plugin-mesh-bridge"], tools: [{ name: "inject_probe", description: "payload arrays into sink contexts" }, { name: "mark_dead", description: "broadcast a dead region to peers" }, { name: "report_finding", description: "publish a finding to the board" }] },
};

const boolDef = (key: string, label: string, description: string, group: string, def: boolean) =>
  ({ key, label, description, kind: "boolean", default: def, choices: undefined, group, operatorOnly: false });
const enumDef = (key: string, label: string, description: string, group: string, choices: string[], def: string) =>
  ({ key, label, description, kind: "enum", default: def, choices, group, operatorOnly: false });

const demoSettingsDefs = [
  boolDef("swarm_autopilot", "Autopilot", "coordinator requeues and summons on its own", "swarm", true),
  enumDef("swarm_turn_cap", "Turn cap", "per-agent turn budget; none = unlimited", "swarm", ["none", "25", "50", "100"], "none"),
  enumDef("complexity", "Complexity", "gates advanced swarm verbs", "swarm", ["basic", "advanced"], "advanced"),
  boolDef("sandbox_pool_warm", "Warm pile", "keep idle boxes ready for instant attach", "sandbox", false),
  enumDef("sandbox_pool_size", "Pool size", "idle boxes kept warm", "sandbox", ["0", "2", "4", "8"], "0"),
  boolDef("transport_scope_guard", "Scope guard", "drop requests outside the engagement scope", "transport", true),
  boolDef("transport_method_guard", "Method guard", "restrict unsafe methods at the transport layer", "transport", true),
  enumDef("transport_rate_limit", "Rate limit", "requests per second ceiling", "transport", ["off", "10", "50", "200"], "off"),
  enumDef("ledger_retest_days", "Retest window", "stale verdicts invite retesting after this many days", "ledger", ["30", "90", "180"], "90"),
  boolDef("memory_preflight_guard", "Memory guard", "refuse spawn plans exceeding the RAM budget", "memory", true),
  boolDef("findings_honest_status", "Honest statuses", "reported never displays as verified without a control test", "findings", true),
];

const demoSettings = {
  settings: Object.fromEntries(demoSettingsDefs.map(d => [d.key, d.default])),
  sources: Object.fromEntries(demoSettingsDefs.map(d => [d.key, "default"])),
  definitions: demoSettingsDefs,
  defaultWriteLayer: "global",
  persisted: false,
};

const DEMO_DASHBOARD = {
  cases: [
    { id: "case-01", target: "https://target.example", targetType: "web-app", latestScanId: "hunt-114", scanCount: 5, familyCount: 2, activeRunCount: 1, reviewCount: 1, openWorkItemCount: 3 },
  ],
  scans: [
    demoScan("hunt-114", "https://target.example", "running", now - 1_800_000),
    demoScan("hunt-110", "https://target.example", "completed", now - 86_400_000),
    demoScan("hunt-102", "https://api.target.example", "completed", now - 172_800_000),
  ],
  groups: [demoGroup],
  workers: [
    demoWorker("agent-1321-54a4", "recon: 47 subdomains, 2 API routes", "running", "recon sweep"),
    demoWorker("agent-1320-d840", "exploit: testing /api/v2 auth bypass", "running", "exploit attempt"),
    demoWorker("agent-1320-2f7f", "warm pile box", "sleeping", null),
    demoWorker("agent-1319-8c1e", "idle", "stopped", null),
  ],
  queue: { runnable: 3, active: 2, blockedByDependency: 0, manualReview: 1, staleWorkers: 0, recoveredClaims: 0 },
};

/** Route a demo response for `path`, or return null to hit the network. */
export function demoResponse(path: string, method = "GET"): unknown {
  const url = path.split("?")[0]!;
  const get = (method.toUpperCase() || "GET") === "GET";
  if (url === "/api/backends") return { backends: [DEMO_DESCRIPTOR] };
  if (/^\/api\/backends\/[^/]+\/handshake$/.test(url)) return { backend: DEMO_DESCRIPTOR, handshake: DEMO_HANDSHAKE };
  if (!get) {
    if (url === "/api/console/sessions") return { session: DEMO_SESSION };
    if (url.startsWith("/api/console/remote-sessions")) return { session: DEMO_SESSION };
    if (/\/configuration$/.test(url)) return { session: DEMO_SESSION };
    if (/\/messages$/.test(url)) return { session: DEMO_SESSION };
    return { ok: true };
  }
  if (url === "/api/dashboard") return DEMO_DASHBOARD;
  if (url === "/api/scans") return { scans: DEMO_DASHBOARD.scans };
  if (/^\/api\/scans\/[^/]+$/.test(url)) return { scan: DEMO_DASHBOARD.scans[0] };
  if (/^\/api\/scans\/[^/]+\/events$/.test(url)) return { scan: DEMO_DASHBOARD.scans[0], events: [] };
  if (/^\/api\/scans\/[^/]+\/findings$/.test(url)) return { scan: DEMO_DASHBOARD.scans[0], findings: [finding], groups: [demoGroup] };
  if (url === "/api/work") return [];
  if (url === "/api/events/recent") return { events: [] };
  if (/^\/api\/finding-family\//.test(url)) return { consoleCommand: "suijin ledger", fingerprint: demoGroup.fingerprint, case: DEMO_DASHBOARD.cases[0], latest: finding, rows: [finding], workflow, workItems: [], artifacts: [] };
  if (url === "/api/console/sessions" && get) return { sessions: [] };
  if (/^\/api\/console\/sessions\/[^/]+$/.test(url)) return { snapshot: DEMO_SNAPSHOT };
  if (/^\/api\/console\/sessions\/[^/]+\/events$/.test(url)) return { events: [], cursor: 0, gap: false };
  if (url === "/api/console/saved") return { sessions: [] };
  if (url === "/api/console/providers") return { providers: demoProviders, preference: { providerId: "z-ai", model: "glm-5.3-flash", connectionIdentity: "demo-zai" } };
  if (url === "/api/console/models") return demoModels;
  if (url === "/api/console/project") return { path: "suijin workspace", name: "Suijin", git: { branch: "main", dirty: false, root: "suijin", error: null } };
  if (url === "/api/skills") return demoSkills;
  if (/^\/api\/skills\/[^/]+$/.test(url)) return { ...demoSkills.skills[0], content: "# recon-sweep\n\nsubfinder → httpx → nuclei surface pass.", files: [] };
  if (url.includes("/console/settings")) return demoSettings;
  if (url.includes("/console/plugins")) return demoPlugins;
  if (url.includes("/console/themes")) return { active: "dark", themes: [{ name: "dark", label: "Suijin dark", description: "abyss teal", mode: "dark", palette: {} }] };
  if (url.includes("/console/checks")) return { project: "suijin", checks: [] };
  if (url.includes("/team")) return { enabled: false, user: null, members: [] };
  return {};
}
