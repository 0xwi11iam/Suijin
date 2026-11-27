// suijin: copied verbatim from 0 packages/engine/src/shared/reproduction.ts @ 78d453c0
/**
 * Proof by local reproduction: data model, wording and claim gate (pure).
 *
 * Two different things live next to a finding and are never mixed up:
 *
 *  - `proof`: a replayable recipe the model proposed. It is derived from the
 *    finding's own steps. Nothing has run it. It is always labelled "proposed".
 *  - `result`: what a harness recorded after it actually ran a recipe against a
 *    local copy of the app in an isolated container. Only the harness writes it,
 *    and the words "reproduced" and "not reproduced" may only come from it.
 *
 * What a result says, precisely: "the app as run returned the canary for this
 * request". It does not say the vulnerability exists in the code, and an app that
 * lies about its responses can forge a hit. "Not reproduced" never means "safe":
 * it means this one recipe did not work on this one commit under these conditions.
 */
import type { PocStep } from "./types.js";

export const REPRODUCTION_STATUSES = ["reproduced", "not-reproduced", "inconclusive", "error"] as const;
export type ReproductionStatus = typeof REPRODUCTION_STATUSES[number];

/** Identifies a record as written by the reproduction harness. */
export const REPRODUCTION_HARNESS = "0security-reproduce/1";

export interface ReproExpect {
  /** HTTP status the model expects from the vulnerable app. */
  status?: number;
  /** Text the model expects in the response (HTTP) or output (shell). */
  bodyContains?: string;
}

export type ReproStep =
  | { id: string; kind: "http"; method: string; /** Path and query only; the host is never taken from the proof. */ path: string; body?: string; expect: ReproExpect }
  | { id: string; kind: "shell"; cmd: string; expect: ReproExpect };

/** A recipe proposed from the model's reasoning. Not run. */
export interface ReproProof {
  schemaVersion: 1;
  label: "proposed";
  /** Always false: only a harness result can say a recipe ran. */
  ran: false;
  source: "poc-steps";
  steps: ReproStep[];
}

export interface ReproductionResult {
  schemaVersion: 1;
  harness: typeof REPRODUCTION_HARNESS;
  status: ReproductionStatus;
  /** Short machine-readable cause, for example canary-observed, canary-absent, app-did-not-start. */
  reason: string;
  /** Git commit of the local copy the recipe ran against. */
  commit?: string;
  /** Digest of the pinned image the run used (`sha256:` and 64 hex digits). */
  imageDigest?: string;
  /** "container" for a run in the isolated runner. "unisolated" marks a host or stand-in run, which can never be reproduced. */
  isolation?: "container" | "unisolated";
  network?: "none" | "loopback";
  canary?: { token: string; observed: boolean };
  /** SHA-256 of the exact recipe that ran, so the result names what it proves. */
  recipeSha256?: string;
  /** SHA-256 over the paths and contents of the app files the run used. */
  appSha256?: string;
  /** True when the operator allowed the run without a writable-storage cap. Absent when the cap applied. */
  storageUnbounded?: true;
  startedAt: string;
  finishedAt: string;
  /** Last 2 KB of output, secrets redacted. */
  logTail: string;
}

export interface FindingReproduction {
  proof?: ReproProof;
  result?: ReproductionResult;
}

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-f]{7,64}$/;
const LOG_LIMIT = 2048;

function isoMs(value: unknown): number | undefined {
  if (typeof value !== "string") return undefined;
  const t = Date.parse(value);
  return Number.isNaN(t) ? undefined : t;
}

/**
 * The only way to turn stored or untrusted data into a result. A "reproduced"
 * or "not-reproduced" status needs the whole harness record: the harness id,
 * a commit, a digest-pinned image, container isolation, a canary, the recipe
 * hash and timestamps. Anything less is absent, never downgraded or guessed.
 */
export function parseReproductionResult(value: unknown): ReproductionResult | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const r = value as Record<string, unknown>;
  if (r.schemaVersion !== 1 || r.harness !== REPRODUCTION_HARNESS) return undefined;
  if (!(REPRODUCTION_STATUSES as readonly string[]).includes(String(r.status))) return undefined;
  if (typeof r.reason !== "string" || typeof r.logTail !== "string") return undefined;
  const start = isoMs(r.startedAt);
  const end = isoMs(r.finishedAt);
  if (start === undefined || end === undefined || end < start) return undefined;
  const status = r.status as ReproductionStatus;
  if (status === "reproduced" || status === "not-reproduced") {
    const canary = r.canary as { token?: unknown; observed?: unknown } | undefined;
    if (typeof r.commit !== "string" || !COMMIT.test(r.commit)) return undefined;
    if (typeof r.imageDigest !== "string" || !SHA256_DIGEST.test(r.imageDigest)) return undefined;
    if (r.isolation !== "container") return undefined;
    if (r.network !== "none" && r.network !== "loopback") return undefined;
    if (typeof r.recipeSha256 !== "string" || !HEX64.test(r.recipeSha256)) return undefined;
    if (typeof r.appSha256 !== "string" || !HEX64.test(r.appSha256)) return undefined;
    if (!canary || typeof canary.token !== "string" || canary.token.length < 8 || typeof canary.observed !== "boolean") return undefined;
    // The status must agree with what the canary showed.
    if ((status === "reproduced") !== canary.observed) return undefined;
  }
  const out: ReproductionResult = {
    schemaVersion: 1, harness: REPRODUCTION_HARNESS, status, reason: r.reason,
    startedAt: r.startedAt as string, finishedAt: r.finishedAt as string,
    logTail: r.logTail.length > LOG_LIMIT ? r.logTail.slice(r.logTail.length - LOG_LIMIT) : r.logTail,
  };
  if (typeof r.commit === "string" && COMMIT.test(r.commit)) out.commit = r.commit;
  if (typeof r.imageDigest === "string" && SHA256_DIGEST.test(r.imageDigest)) out.imageDigest = r.imageDigest;
  if (r.isolation === "container" || r.isolation === "unisolated") out.isolation = r.isolation;
  if (r.network === "none" || r.network === "loopback") out.network = r.network;
  if (typeof r.recipeSha256 === "string" && HEX64.test(r.recipeSha256)) out.recipeSha256 = r.recipeSha256;
  if (typeof r.appSha256 === "string" && HEX64.test(r.appSha256)) out.appSha256 = r.appSha256;
  if (r.storageUnbounded === true) out.storageUnbounded = true;
  const canary = r.canary as { token?: unknown; observed?: unknown } | undefined;
  if (canary && typeof canary.token === "string" && typeof canary.observed === "boolean") out.canary = { token: canary.token, observed: canary.observed };
  return out;
}

/** True only for a complete harness record that observed the canary. */
export function isReproduced(result: unknown): boolean {
  return parseReproductionResult(result)?.status === "reproduced";
}

function parseStep(value: unknown): ReproStep | undefined {
  if (!value || typeof value !== "object") return undefined;
  const s = value as Record<string, unknown>;
  if (typeof s.id !== "string" || !s.expect || typeof s.expect !== "object") return undefined;
  const e = s.expect as Record<string, unknown>;
  const expect: ReproExpect = {
    ...(typeof e.status === "number" ? { status: e.status } : {}),
    ...(typeof e.bodyContains === "string" ? { bodyContains: e.bodyContains } : {}),
  };
  if (s.kind === "http" && typeof s.method === "string" && typeof s.path === "string") {
    return { id: s.id, kind: "http", method: s.method, path: s.path, ...(typeof s.body === "string" ? { body: s.body } : {}), expect };
  }
  if (s.kind === "shell" && typeof s.cmd === "string") return { id: s.id, kind: "shell", cmd: s.cmd, expect };
  return undefined;
}

/** A stored proof is always read back as proposed and not run, whatever the text says. */
export function parseReproProof(value: unknown): ReproProof | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const p = value as Record<string, unknown>;
  if (!Array.isArray(p.steps)) return undefined;
  const steps = p.steps.map(parseStep).filter((s): s is ReproStep => s !== undefined);
  if (steps.length === 0) return undefined;
  return { schemaVersion: 1, label: "proposed", ran: false, source: "poc-steps", steps };
}

/** Stored text to a record. Unusable parts are dropped; an empty record is absent. */
export function parseFindingReproduction(value: unknown): FindingReproduction | undefined {
  let v = value;
  if (typeof v === "string") {
    if (!v) return undefined;
    try { v = JSON.parse(v); } catch { return undefined; }
  }
  if (!v || typeof v !== "object" || Array.isArray(v)) return undefined;
  const raw = v as Record<string, unknown>;
  const proof = parseReproProof(raw.proof);
  const result = parseReproductionResult(raw.result);
  if (!proof && !result) return undefined;
  return { ...(proof ? { proof } : {}), ...(result ? { result } : {}) };
}

function pathOf(url: string): string {
  try {
    const u = new URL(url);
    return `${u.pathname}${u.search}` || "/";
  } catch {
    return url.startsWith("/") ? url : `/${url}`;
  }
}

function expectOf(step: PocStep): ReproExpect {
  const e = step.expect;
  if (!e) return {};
  if (e.type === "body-contains") return { bodyContains: e.text };
  if (e.type === "http-status") return { status: Array.isArray(e.status) ? e.status[0] : e.status };
  return {};
}

/**
 * A proposed recipe from the finding's own steps: its http and shell actions
 * with what the model expected to see. A step without its own expectation takes
 * the first expectation found on a later step. Returns undefined when there is
 * nothing replayable. Nothing here runs anything.
 */
export function proposeProof(finding: { pocSteps?: PocStep[] }): ReproProof | undefined {
  const all = finding.pocSteps ?? [];
  const steps: ReproStep[] = [];
  all.forEach((step, index) => {
    let expect = expectOf(step);
    if (Object.keys(expect).length === 0) {
      for (const later of all.slice(index + 1)) {
        const found = expectOf(later);
        if (Object.keys(found).length > 0) { expect = found; break; }
      }
    }
    if (step.action.type === "http") steps.push({ id: step.id, kind: "http", method: step.action.method.toUpperCase(), path: pathOf(step.action.url), ...(step.action.body ? { body: step.action.body } : {}), expect });
    else if (step.action.type === "shell") steps.push({ id: step.id, kind: "shell", cmd: step.action.cmd, expect });
  });
  return steps.length > 0 ? { schemaVersion: 1, label: "proposed", ran: false, source: "poc-steps", steps } : undefined;
}

const short = (hex: string, n = 12) => hex.replace(/^sha256:/, "").slice(0, n);

/** The one honest line for a result. Only a complete harness record earns "Reproduced". */
export function reproductionLine(result: ReproductionResult | undefined): string {
  const r = parseReproductionResult(result);
  if (!r) return "No reproduction has been run";
  const where = r.commit && r.imageDigest ? `on commit ${r.commit.slice(0, 12)} in an isolated container (image ${short(r.imageDigest)})` : "";
  switch (r.status) {
    case "reproduced": return `Reproduced: the app as run returned the canary for this request, ${where}. This is an observation of the running app, not a statement about the code`;
    case "not-reproduced": return `Not reproduced: the app as run did not return the canary for this request, ${where}. This does not mean the issue is absent or the code is safe`;
    case "inconclusive": return `Inconclusive: the recipe could not show a result (${r.reason.replace(/-/g, " ")})${r.isolation === "unisolated" ? ". The run was not isolated, so it cannot count as reproduced" : ""}. This does not mean the issue is absent`;
    default: return `Reproduction did not run: ${r.reason.replace(/-/g, " ")}`;
  }
}

/** The line for a proposed recipe that nothing has run. */
export function proofLine(proof: ReproProof | undefined): string | undefined {
  if (!proof) return undefined;
  const n = proof.steps.length;
  return `Proposed steps, not run (${n} step${n === 1 ? "" : "s"}). Nothing has checked them`;
}

/** Short label for a list row. */
export function reproductionLabel(rep: FindingReproduction | undefined): string | undefined {
  if (!rep) return undefined;
  if (rep.result) {
    switch (rep.result.status) {
      case "reproduced": return "reproduced in a container";
      case "not-reproduced": return "not reproduced (not proof of safety)";
      case "inconclusive": return "reproduction inconclusive";
      default: return "reproduction error";
    }
  }
  return rep.proof ? "proposed steps, not run" : undefined;
}

const REPRO_CLAIM = /\b(executed[- ]proven|proven by execution|reproduced|reproduces|successfully exploited|exploit (was )?confirmed|confirmed exploitable|proven exploitable|proof of exploit)\b/i;
const REPRO_NEGATED = /\b(not|never|no|n't|unable to|could not|couldn't|cannot|can't|nothing)\b[^.\n]{0,24}\b(reproduced|reproduces|exploited|confirmed|proven)\b/i;

/** True when text claims the issue was reproduced or exploited. */
export function claimsReproduced(text: string): boolean {
  return REPRO_CLAIM.test(text) && !REPRO_NEGATED.test(text);
}

/**
 * A "reproduced" claim stands only with a complete harness result that
 * observed the canary. Otherwise the text is kept and marked as not backed.
 */
export function gateReproductionClaim(text: string, result: ReproductionResult | undefined): { allowed: boolean; message: string } {
  const body = text.trimEnd();
  if (!claimsReproduced(body)) return { allowed: true, message: body };
  if (isReproduced(result)) return { allowed: true, message: `${body}\n\n${reproductionLine(result)}` };
  return { allowed: false, message: `${body}\n\nThe claim above is not backed by a harness result. ${reproductionLine(result)}` };
}

/**
 * What to show for a finding: the stored record, with a proposed proof derived
 * from the finding's steps when none is stored. The result is only ever the
 * stored harness result.
 */
export function reproductionFor(finding: { pocSteps?: PocStep[] }, stored: FindingReproduction | undefined): FindingReproduction | undefined {
  const proof = stored?.proof ?? proposeProof(finding);
  const result = stored?.result;
  if (!proof && !result) return undefined;
  return { ...(proof ? { proof } : {}), ...(result ? { result } : {}) };
}
