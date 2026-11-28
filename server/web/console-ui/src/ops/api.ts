/**
 * The operations API and the data hub, typed — everything Suijin knows about itself.
 *
 * Reading is polled (the console is a polling console); every action goes through the same
 * routes the terminal's control hub uses, and invalidates everything under "ops".
 */
import { useCallback, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useBackendApi } from "@/api";
import { useFlash } from "./ui";

// ── shapes ────────────────────────────────────────────────────────────────────────────────

export interface OpsAgent {
  id: string;
  swarm?: string | null;
  status?: string | null;
  current_intent?: string | null;
  lane?: string;
  last_heartbeat?: number | null;
  joined_at?: number | null;
  hb_age?: number | null;
  alive?: boolean;
  boxed?: boolean;
  tier?: string;
  [key: string]: unknown;
}

export interface OpsIntent {
  id: string;
  text: string;
  state?: string | null;
  agent_id?: string | null;
  swarm?: string | null;
  engagement?: string | null;
  attempts?: number | null;
  created_at?: number | null;
  claimed_at?: number | null;
  completed_at?: number | null;
  age_s?: number | null;
  held_s?: number | null;
}

export interface OpsSandbox {
  name: string;
  status: string;
  age: string;
  running: boolean;
  pooled: boolean;
  engagement: string;
  identity: string;
}

export interface OpsBoxes {
  engine: string;
  engine_socket?: string;
  sandboxes?: OpsSandbox[];
  pool?: { size: number; target: number; max?: number; members?: OpsSandbox[] };
  image?: { name: string; tag: string; exists: boolean; fresh: boolean | null };
  box?: { engine: string; name: string; adopted: boolean; state: string; status?: string; note?: string };
  checked_at?: string;
  took_ms?: number;
  stale?: boolean;
  loading?: boolean;
  error?: string;
}

export interface ImageBuild {
  state: "idle" | "running" | "done" | "failed";
  started_at?: number | null;
  finished_at?: number | null;
  log: string[];
  error?: string;
  tag?: string;
}

export interface OpsMeshChat {
  ts: string;
  src: string;
  engagement: string;
  message: string;
}

export interface OpsOverview {
  at: string;
  coordinator: { running: boolean; bus: string; log: string[]; log_updated: string };
  agents: OpsAgent[];
  intents: OpsIntent[];
  fleet: {
    pause_flag: boolean;
    autopilot: { enabled: boolean; blocked: boolean; note: string };
    turn_cap: number;
    pool_target: number;
    models: { leader: string; supervisor: string; worker: string; planner: string; worker_route: string };
    operator_mode: string;
  };
  findings: { total: number; confirmed: number; by_severity: Record<string, number>; latest: { severity: string; status: string; title: string }[] };
  mesh: { peers: Record<string, unknown>[]; chat: OpsMeshChat[] };
  jobs: Record<string, unknown>[];
  daemons: Record<string, unknown>[];
  browser: { running: boolean; port: number | null };
  system: Record<string, unknown>;
  sessions: { id: string; kind?: string; title?: string; status?: string }[];
  structured: StructuredTeam[];
  boxes: OpsBoxes;
  image_build: ImageBuild;
  counts: Record<string, number>;
  errors: Record<string, string>;
}

export interface StructuredTeam {
  slug: string;
  file: string;
  leader: string;
  supervisors: { role: string; id: string }[];
  workers: { role: string; id: string }[];
  updated: string;
  live: number;
}

export interface EngagementRow {
  id: string;
  kind: string;
  name: string;
  target: string;
  permissions: string;
  mode: string;
  createdAt: string;
  events: number;
  findings: number;
  exploits: number;
  files: number;
  bytes: number;
  inbox: boolean;
  reports: boolean;
  notes: boolean;
  audit_trail: boolean;
  lastActivity: string;
  live_agents?: number;
}

export interface FileRow {
  path: string;
  bytes: number;
  modified: string;
}

export interface EngagementDetail {
  id: string;
  meta: Record<string, unknown>;
  kind: string;
  events: { lines: number; kinds: Record<string, number>; agents: Record<string, number> };
  inbox: FileRow[];
  reports: FileRow[];
  notes: FileRow[];
  exploits: { id: string; title: string; class: string; severity: string; cvss: number; status: string; attempts: number; last_verdict: string }[];
  exploit_files: FileRow[];
  state: FileRow[];
  audit: Record<string, Record<string, unknown> | null>;
  findings: { id: string; title: string; severity: string; status: string; created_at: number; exp_id?: string; cvss?: number | null }[];
  files: FileRow[];
}

export interface Page<T> {
  store?: string;
  rows: T[];
  offset?: number;
  limit?: number;
  total?: number | null;
  columns?: string[];
  kinds?: { kind: string; rows: number }[];
  error?: string;
}

export interface StoreInfo {
  id: string;
  group: string;
  label: string;
  kind: string;
  where: string;
  description: string;
  sensitivity: "public" | "operator" | "secret";
  columns: string[];
  filterable: string[];
  volatile: boolean;
  rows: number | null;
}

export interface Catalog {
  stores: StoreInfo[];
  groups: { id: string; stores: number; rows: number }[];
}

// ── access ────────────────────────────────────────────────────────────────────────────────

const request = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
});

/** The typed door. `get` takes a query object; `send` is for the control routes. */
export function useOps() {
  const { webFetchJson, client } = useBackendApi();
  return useMemo(() => ({
    get: <T,>(path: string, query?: Record<string, string | number | undefined>, signal?: AbortSignal) => {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query ?? {})) if (value !== undefined && value !== "") params.set(key, String(value));
      const suffix = params.size ? `?${params}` : "";
      return webFetchJson<T>(`${path}${suffix}`, { signal });
    },
    send: <T,>(path: string, method: "POST" | "DELETE", body?: unknown) => webFetchJson<T>(path, request(method, body)),
  }), [webFetchJson, client.backendId]);
}

export const OPS_KEY = ["ops"] as const;

export function useOverview(interval = 3000) {
  const ops = useOps();
  return useQuery({ queryKey: [...OPS_KEY, "overview"], queryFn: ({ signal }) => ops.get<OpsOverview>("/api/ops/overview", undefined, signal), refetchInterval: interval });
}

export function useEngagements() {
  const ops = useOps();
  return useQuery({ queryKey: [...OPS_KEY, "engagements"], queryFn: ({ signal }) => ops.get<{ engagements: EngagementRow[] }>("/api/ops/engagements", undefined, signal), refetchInterval: 5000 });
}

export function useEngagement(id: string | undefined) {
  const ops = useOps();
  return useQuery({ queryKey: [...OPS_KEY, "engagement", id], queryFn: ({ signal }) => ops.get<EngagementDetail>(`/api/ops/engagements/${encodeURIComponent(id ?? "")}`, undefined, signal), enabled: Boolean(id), refetchInterval: 5000 });
}

export function useEngagementEvents(id: string | undefined, kind: string, offset: number, q: string) {
  const ops = useOps();
  return useQuery({
    queryKey: [...OPS_KEY, "events", id, kind, offset, q],
    queryFn: ({ signal }) => ops.get<Page<Record<string, unknown>>>(`/api/ops/engagements/${encodeURIComponent(id ?? "")}/events`, { kind, offset, limit: 100, q: q || undefined }, signal),
    enabled: Boolean(id),
    refetchInterval: kind || q ? false : 4000,
  });
}

export function useEngagementFile(id: string | undefined, name: string | null) {
  const ops = useOps();
  return useQuery({
    queryKey: [...OPS_KEY, "file", id, name],
    queryFn: ({ signal }) => ops.get<Page<{ name: string; content: string; bytes: number; truncated?: boolean; binary?: boolean }>>(
      `/api/ops/engagements/${encodeURIComponent(id ?? "")}/file`, { name: name ?? "" }, signal),
    enabled: Boolean(id && name),
    staleTime: 10_000,
  });
}

export function useCatalog() {
  const ops = useOps();
  return useQuery({ queryKey: [...OPS_KEY, "catalog"], queryFn: ({ signal }) => ops.get<Catalog>("/api/data/catalog", undefined, signal), refetchInterval: 8000 });
}

export function useStoreRows(storeId: string | null, q: string, kind: string | undefined, offset: number) {
  const ops = useOps();
  return useQuery({
    queryKey: [...OPS_KEY, "store", storeId, q, kind, offset],
    queryFn: ({ signal }) => ops.get<Page<Record<string, unknown>>>(`/api/data/stores/${encodeURIComponent(storeId ?? "")}`, { q: q || undefined, kind, offset, limit: 100 }, signal),
    enabled: Boolean(storeId),
    refetchInterval: 6000,
  });
}

export function useStoreKinds(storeId: string | null) {
  const ops = useOps();
  return useQuery({
    queryKey: [...OPS_KEY, "kinds", storeId],
    queryFn: ({ signal }) => ops.get<{ kinds: { kind: string; rows: number }[] }>(`/api/data/stores/${encodeURIComponent(storeId ?? "")}/kinds`, undefined, signal),
    enabled: Boolean(storeId),
    staleTime: 30_000,
  });
}

/** A control action. Every success and failure is returned to the caller to show. */
export function useOpsAction() {
  const ops = useOps();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ path, method, body }: { path: string; method: "POST" | "DELETE"; body?: unknown }) => ops.send<unknown>(path, method, body),
    onSettled: () => queryClient.invalidateQueries({ queryKey: OPS_KEY }),
  });
}

/** Run one control action and say what came back — the route's own note when it has one. */
export function useRun() {
  const action = useOpsAction();
  const { show } = useFlash();
  const run = useCallback(async <T,>(path: string, body?: unknown, options: { method?: "POST" | "DELETE"; ok?: string } = {}) => {
    try {
      const result = (await action.mutateAsync({ path, method: options.method ?? "POST", body })) as T;
      const note = (result as { note?: string } | null)?.note;
      const refused = Boolean(note && /did not accept/.test(note));
      show(note || options.ok || "Done", refused ? "error" : "status");
      return result;
    } catch (error) {
      show(error instanceof Error ? error.message : String(error), "error");
      return null;
    }
  }, [action, show]);
  return { run, pending: action.isPending };
}
