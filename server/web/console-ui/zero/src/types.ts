import type { ImpactAssessment, PresentationEvent } from "@0/engine/shared";
import type { FindingReproduction } from "@zero/engine/reproduction";

export type ScanSummary = {
  totalFindings: number;
  totalAttacks?: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
};

export type FindingWorkflowStatus =
  | "backlog"
  | "todo"
  | "agent_review"
  | "in_progress"
  | "human_review"
  | "blocked"
  | "done"
  | "cancelled";

export type FindingWorkflowPhase =
  | "backlog"
  | "todo"
  | "in_progress"
  | "blocked"
  | "done"
  | "cancelled";

export type FindingReviewGate = "none" | "agent_review" | "human_review";

export type FindingConsensus = "verified" | "false-positive" | "disputed" | "pending";

export type ScanRecord = {
  id: string;
  target: string;
  depth: string;
  runtime: string;
  mode: string;
  status: string;
  startedAt: string;
  completedAt: string | null;
  durationMs: number | null;
  summary: ScanSummary;
  ciImport?: {
    trust: "unverified";
    digest: string;
    importedAt: string;
    sourceExecutionStatus: "successful" | "failed" | "unknown";
    repository: string | null;
    reviewedRevision: string | null;
    githubEventRevision: string | null;
    runId: string | null;
    cliVersion: string | null;
    cliExitCode: number | null;
  };
};

export type FindingRecord = {
  id: string;
  scanId: string;
  title: string;
  description: string;
  severity: string;
  category: string;
  status: string;
  fingerprint?: string | null;
  triageStatus: "new" | "accepted" | "suppressed";
  triageNote?: string | null;
  workflowStatus?: FindingWorkflowStatus;
  workflowAssignee?: string | null;
  workflowUpdatedAt?: string | null;
  /** Scanner the finding was imported from; absent for findings 0 found. */
  tool?: string | null;
  timestamp: number;
  score?: number | null;
  confidence?: number | null;
  cvssScore?: number | null;
  cvssVector?: string | null;
  evidenceRequest: string;
  evidenceResponse: string;
  evidenceAnalysis?: string | null;
  impactAssessment?: ImpactAssessment | null;
  businessContext?: import("@0/engine/shared").FindingBusinessContext | null;
  /** Proposed recipe (never run) and the harness result, when there is one. */
  reproduction?: FindingReproduction | null;
  /** Executed-PoC record for a high or critical finding; a derived static-only record when none was attempted. Absent for lower severities. */
  executedPoc?: import("@zero/engine/executed-poc").ExecutedPoc | null;
};

export type FindingWorkflowSummary = {
  status: FindingWorkflowStatus;
  persistedStatus: FindingWorkflowStatus;
  recommendedStatus: FindingWorkflowStatus;
  phase: FindingWorkflowPhase;
  reviewGate: FindingReviewGate;
  reviewReason: string | null;
  assignee: string | null;
  updatedAt: string | null;
  consensus: FindingConsensus;
  activeAgentRoles: string[];
  evidenceSignal: "weak" | "medium" | "strong";
  verdictCounts: {
    truePositive: number;
    falsePositive: number;
    unsure: number;
    total: number;
  };
};

export type CaseSummary = {
  id: string;
  target: string;
  targetType: "ai-app" | "package" | "repository" | "web-app" | "unknown";
  latestScanId: string | null;
  scanCount: number;
  familyCount: number;
  activeRunCount: number;
  reviewCount: number;
  openWorkItemCount: number;
};

export type WorkItemSummary = {
  id: string;
  kind: "surface_map" | "hypothesis" | "poc_build" | "blind_verify" | "consensus" | "human_review";
  title: string;
  owner: string | null;
  status: "backlog" | "todo" | "in_progress" | "blocked" | "done" | "cancelled";
  summary: string;
};

export type WorkerSummary = {
  id: string;
  role: "orchestrator";
  status: "idle" | "claiming" | "running" | "sleeping" | "stopped" | "error";
  label: string;
  currentCaseId: string | null;
  currentCaseTarget: string | null;
  currentWorkItemId: string | null;
  currentWorkItemTitle: string | null;
  currentWorkItemKind: "surface_map" | "hypothesis" | "poc_build" | "blind_verify" | "consensus" | "human_review" | null;
  currentScanId: string | null;
  pid: number | null;
  host: string | null;
  lastError: string | null;
  heartbeatAt: string;
  startedAt: string;
  updatedAt: string;
  isActive: boolean;
};

export type QueueSummary = {
  runnable: number;
  active: number;
  blockedByDependency: number;
  manualReview: number;
  staleWorkers: number;
  recoveredClaims: number;
};

export type ArtifactSummary = {
  id: string;
  kind: "request" | "response" | "analysis" | "verdicts" | "sessions" | "events";
  label: string;
  summary: string;
};

/** Where a finding came from, as the engine recorded it. */
export type FindingProvenance =
  | { kind: "scan" | "thread" | "ci-import"; target: string; startedAt: string; scanId: string }
  | { kind: "agent"; agent: string; target: string; startedAt: string; scanId: string }
  | { kind: "unknown"; scanId: string };

export type FindingGroup = {
  fingerprint: string;
  latest: FindingRecord;
  count: number;
  scanCount: number;
  /** All originating scans; optional for compatibility with older engines. */
  sourceScanIds?: string[];
  provenance?: FindingProvenance | null;
  workflow: FindingWorkflowSummary;
};

export type DashboardResponse = {
  cases: CaseSummary[];
  scans: ScanRecord[];
  groups: FindingGroup[];
  workers: WorkerSummary[];
  queue: QueueSummary;
};

/** Where the finding was cited in source, from its stored review annotation. The file itself is never read. */
export type FindingLocation = { path: string; startLine: number; endLine?: number; suggestion?: string };

/** One fix `0security findings fix` recorded. Only what the CLI stored, plus the local fix commit when the branch still exists. */
export type FindingFixRecord = {
  branch: string | null;
  result: string;
  pullRequestUrl: string | null;
  /** How the pull request was opened. Merged and closed are not recorded. */
  draft: boolean | null;
  /** The checks named with --test when the fix ran, on the machine that ran it. Not the code host's CI. */
  checksPassing: boolean | null;
  recordedAt: string;
  diff?: string;
  diffTruncated?: boolean;
  /** Code-host CI snapshot; local --test results remain separate. */
  ci?: { status: "passing" | "failing" | "pending" | "none" | "unavailable"; checkedAt: string; url?: string };
};

export type FindingFixInfo = { records: FindingFixRecord[]; command: string };

export type FindingFamilyResponse = {
  /** Optional: absent from older engines. */
  location?: FindingLocation | null;
  fix?: FindingFixInfo;
  consoleCommand: string;
  fingerprint: string;
  case: CaseSummary | null;
  latest: FindingRecord;
  rows: FindingRecord[];
  provenance?: FindingProvenance | null;
  workflow: FindingWorkflowSummary;
  workItems: WorkItemSummary[];
  artifacts: ArtifactSummary[];
};

export type RecentEventsResponse = {
  events: Array<{
    id: string;
    scanId: string;
    scanTarget: string;
    stage: string;
    eventType: string;
    findingId?: string | null;
    findingFingerprint?: string | null;
    agentRole?: string | null;
    summary: string;
    payload: Record<string, unknown> | null;
    timestamp: number;
    presentation: PresentationEvent;
  }>;
};

export type ScanFindingsResponse = {
  scan: ScanRecord;
  findings: FindingRecord[];
  groups: FindingGroup[];
};
