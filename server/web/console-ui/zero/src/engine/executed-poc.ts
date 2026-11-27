// suijin: copied verbatim from 0 packages/engine/src/shared/executed-poc.ts @ 78d453c0
/**
 * Executed proof of concept: data model, wording and claim rules (pure).
 *
 * After a high or critical finding is saved, the harness tries to build a script
 * from the finding's own steps and run it in the sandbox. The script is written by
 * the model, so a run shows what the script printed, not that the code is vulnerable:
 * a script can print its expected text without touching the vulnerable code.
 * Three outcomes only:
 *
 *  - `script-matched`: the model-authored script ran in a container with the network
 *    off and printed the expected text. Needs a complete container run record. It is
 *    not proof; only a reproduction result written by the reproduction harness
 *    (`reproduced`, with a canary the harness planted) is.
 *  - `script-unmatched`: the script ran in a container and the expected text did not
 *    appear. This never means the finding is false; this script did not work here.
 *  - `static-only`: nothing ran to a verdict (no runnable steps, no sandbox, budget
 *    spent, or the run could not finish). The finding stands on its static evidence.
 *
 * Only the harness writes this record. The parser refuses a `script-matched`
 * record that lacks container isolation, a pinned image digest and a passing run.
 * Records stored under the first names (`executed-proven`, `executed-failed`) read
 * back as `script-matched` and `script-unmatched`.
 */

export const EXECUTED_POC_OUTCOMES = ["script-matched", "script-unmatched", "static-only"] as const;
export type ExecutedPocOutcome = typeof EXECUTED_POC_OUTCOMES[number];

/** Identifies a record as written by the executed-PoC harness. */
export const EXECUTED_POC_HARNESS = "0security-executed-poc/1";

/** The verifier run behind an executed outcome: the same fields the verifier records. */
export interface ExecutedPocRun {
  status: "pass" | "fail" | "not-run" | "error";
  reason: string;
  command: string;
  isolation: "container" | "host";
  /** `sha256:` and 64 hex digits of the pinned image. */
  imageDigest?: string;
  durationMs: number;
  /** Last 2 KB of output, secrets redacted. */
  logTail: string;
  exitCode?: number | null;
}

export interface ExecutedPoc {
  schemaVersion: 1;
  harness: typeof EXECUTED_POC_HARNESS;
  outcome: ExecutedPocOutcome;
  /** Short machine-readable cause, for example expectation-met, expectation-not-met, no-sandbox-image, budget-exhausted. */
  reason: string;
  /** Ids of the PoC steps that were run. Empty for static-only. */
  stepIds: string[];
  /** Present for executed outcomes and for runs that could not finish. */
  run?: ExecutedPocRun;
  startedAt: string;
  finishedAt: string;
}

const LEGACY_OUTCOMES: Record<string, ExecutedPocOutcome> = { "executed-proven": "script-matched", "executed-failed": "script-unmatched" };

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;
const LOG_LIMIT = 2048;
const RUN_STATUSES = ["pass", "fail", "not-run", "error"];

function isoMs(value: unknown): number | undefined {
  if (typeof value !== "string") return undefined;
  const t = Date.parse(value);
  return Number.isNaN(t) ? undefined : t;
}

function parseRun(value: unknown): ExecutedPocRun | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const r = value as Record<string, unknown>;
  if (!RUN_STATUSES.includes(String(r.status)) || typeof r.reason !== "string" || typeof r.command !== "string") return undefined;
  if (r.isolation !== "container" && r.isolation !== "host") return undefined;
  if (typeof r.durationMs !== "number" || typeof r.logTail !== "string") return undefined;
  return {
    status: r.status as ExecutedPocRun["status"], reason: r.reason, command: r.command.slice(0, 4096), isolation: r.isolation, durationMs: r.durationMs,
    logTail: r.logTail.length > LOG_LIMIT ? r.logTail.slice(r.logTail.length - LOG_LIMIT) : r.logTail,
    ...(typeof r.imageDigest === "string" && SHA256_DIGEST.test(r.imageDigest) ? { imageDigest: r.imageDigest } : {}),
    ...(typeof r.exitCode === "number" || r.exitCode === null ? { exitCode: r.exitCode as number | null } : {}),
  };
}

/**
 * The only way to turn stored or untrusted data into a record. An executed
 * outcome needs a run record that agrees with it; `script-matched` also needs
 * container isolation and a pinned image digest. Anything less is absent,
 * never downgraded or guessed.
 */
export function parseExecutedPoc(value: unknown): ExecutedPoc | undefined {
  let v = value;
  if (typeof v === "string") {
    if (!v) return undefined;
    try { v = JSON.parse(v); } catch { return undefined; }
  }
  if (!v || typeof v !== "object" || Array.isArray(v)) return undefined;
  const r = v as Record<string, unknown>;
  if (r.schemaVersion !== 1 || r.harness !== EXECUTED_POC_HARNESS) return undefined;
  const named = LEGACY_OUTCOMES[String(r.outcome)] ?? String(r.outcome);
  if (!(EXECUTED_POC_OUTCOMES as readonly string[]).includes(named)) return undefined;
  if (typeof r.reason !== "string") return undefined;
  const start = isoMs(r.startedAt);
  const end = isoMs(r.finishedAt);
  if (start === undefined || end === undefined || end < start) return undefined;
  const outcome = named as ExecutedPocOutcome;
  const run = parseRun(r.run);
  if (outcome === "script-matched") {
    if (!run || run.status !== "pass" || run.isolation !== "container" || !run.imageDigest) return undefined;
  }
  if (outcome === "script-unmatched" && (!run || run.status !== "fail" || run.isolation !== "container")) return undefined;
  if (outcome === "static-only" && run && run.status === "pass") return undefined;
  const stepIds = Array.isArray(r.stepIds) ? r.stepIds.filter((s): s is string => typeof s === "string").slice(0, 50) : [];
  return {
    schemaVersion: 1, harness: EXECUTED_POC_HARNESS, outcome, reason: r.reason, stepIds,
    ...(run ? { run } : {}), startedAt: r.startedAt as string, finishedAt: r.finishedAt as string,
  };
}

/** True only for a complete harness record of a passing container run of the model's script. Not proof of a vulnerability. */
export function isScriptMatched(value: unknown): boolean {
  return parseExecutedPoc(value)?.outcome === "script-matched";
}

/** Findings of these severities get a PoC attempt. */
export function isPocEligibleSeverity(severity: string | undefined): boolean {
  return severity === "high" || severity === "critical";
}

const short = (hex: string, n = 12) => hex.replace(/^sha256:/, "").slice(0, n);
const why = (reason: string) => reason.replace(/-/g, " ");

/**
 * What to show for a finding: the stored record, or for a high or critical
 * finding with none, a derived static-only record that says no PoC was
 * attempted. Lower severities show nothing. Never derives a proven outcome.
 */
export function executedPocFor(finding: { severity?: string }, stored: ExecutedPoc | undefined): ExecutedPoc | undefined {
  if (stored) return stored;
  if (!isPocEligibleSeverity(finding.severity)) return undefined;
  return { schemaVersion: 1, harness: EXECUTED_POC_HARNESS, outcome: "static-only", reason: "not-attempted", stepIds: [], startedAt: "", finishedAt: "" };
}

/** Short label for a list row. */
export function executedPocLabel(record: ExecutedPoc | undefined): string | undefined {
  if (!record) return undefined;
  switch (record.outcome) {
    case "script-matched": return "script-matched (model-written script ran in the sandbox; not a reproduction)";
    case "script-unmatched": return "script-unmatched (script ran and did not match; not proof of safety)";
    default: return "static-only (nothing ran)";
  }
}

/** The one honest line for a record. It never says proven or reproduced: the script is model-written. */
export function executedPocLine(record: ExecutedPoc | undefined): string {
  if (!record) return "No PoC execution was recorded";
  const image = record.run?.imageDigest ? ` (image ${short(record.run.imageDigest)})` : "";
  switch (record.outcome) {
    case "script-matched": return `Script matched: a script written by the model ran in a container with the network off and printed the expected text${image}. The script may not have touched the vulnerable code, so this is not a reproduction`;
    case "script-unmatched": return `Script unmatched: a script written by the model ran in a container and the expected text did not appear${image}. This does not mean the issue is absent`;
    default: return `Static only: nothing ran to a verdict (${why(record.reason)}). The finding rests on static evidence`;
  }
}
