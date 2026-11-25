import { Tooltip } from "@/components/ui/tooltip";
import { Disclosure } from "@/components/ui/disclosure";
import { getFindingPriority } from "@0/engine/dist/shared/finding-priority.js";
import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  ChevronDown,
  Copy,
  Download,
  MessageSquare,
  Search,
  ShieldCheck,
  ShieldOff,
  ShieldQuestion,
  SlidersHorizontal,
} from "lucide-react";
import { useBackendApi } from "@/api";
import { InspectorPane } from "@/components/inspector-pane";
import { PageHeader, PageContent } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/state-panel";
import {
  CvssBadge,
  ExpIdBadge,
  StatusBadge,
} from "@/components/status-badges";
import {
  Card,
  CardContent,
  CardHeader,
  CardList,
  CardListItem,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { formatTime } from "@/lib/format";
import { usePersistentState } from "@/lib/use-persistent-state";
import type {
  DashboardResponse,
  FindingGroup,
  FindingConsensus,
  FindingFamilyResponse,
  FindingReviewGate,
  FindingWorkflowPhase,
  FindingWorkflowStatus,
  FindingWorkflowSummary,
} from "@/types";

const WORKFLOW_ACTIONS: Array<{
  value: FindingWorkflowPhase;
  label: string;
}> = [
  { value: "backlog", label: "Backlog" },
  { value: "todo", label: "Todo" },
  { value: "in_progress", label: "In progress" },
  { value: "blocked", label: "Blocked" },
  { value: "done", label: "Done" },
  { value: "cancelled", label: "Cancelled" },
];

const CONSENSUS_OPTIONS: Array<{ value: "all" | FindingConsensus; label: string }> = [
  { value: "all", label: "All" },
  { value: "pending", label: "No verifier votes" },
  { value: "verified", label: "Positive verifier votes" },
  { value: "disputed", label: "Mixed or unsure votes" },
  { value: "false-positive", label: "Negative verifier votes" },
];

type WorkflowMutationInput = {
  fingerprint: string;
  workflowStatus: FindingWorkflowStatus;
  workflowAssignee: string;
  optimisticStatus?: FindingWorkflowStatus;
};

type ThreadViewMode = "inbox" | "review" | "board";
type QueueSortMode = "business-impact" | "attention" | "newest" | "severity";
type ThreadConsoleState = {
  search: string;
  workflowFilter: "all" | FindingWorkflowPhase;
  reviewFilter: "all" | FindingReviewGate;
  severityFilter: string;
  consensusFilter: "all" | FindingConsensus;
  assigneeFilter: string;
  activeOnly: boolean;
  viewMode: ThreadViewMode;
  queueSort: QueueSortMode;
};

const DEFAULT_THREAD_CONSOLE_STATE: ThreadConsoleState = {
  search: "",
  workflowFilter: "all",
  reviewFilter: "all",
  severityFilter: "all",
  consensusFilter: "all",
  assigneeFilter: "all",
  activeOnly: false,
  viewMode: "inbox",
  queueSort: "severity",
};

function matchesSearch(haystack: DashboardResponse["groups"][number], normalized: string) {
  return [
    haystack.latest.title,
    getFindingPriority(haystack.latest).label,
    getFindingPriority(haystack.latest).rationale,
    haystack.latest.impactAssessment?.blast_radius ?? "",
    haystack.latest.category,
    haystack.latest.severity,
    haystack.latest.triageStatus,
    haystack.workflow.status,
    haystack.workflow.phase,
    haystack.workflow.reviewGate,
    haystack.workflow.persistedStatus,
    haystack.workflow.recommendedStatus,
    haystack.workflow.reviewReason ?? "",
    haystack.workflow.consensus,
    haystack.workflow.assignee ?? "",
    haystack.workflow.activeAgentRoles.join(" "),
    haystack.fingerprint,
  ]
    .join(" ")
    .toLowerCase()
    .includes(normalized);
}

function patchWorkflowSummary(
  workflow: FindingWorkflowSummary,
  variables: WorkflowMutationInput,
): FindingWorkflowSummary {
  const nextAssignee = variables.workflowAssignee.trim() || null;
  const nextRecommendedStatus =
    variables.workflowStatus === "done" || variables.workflowStatus === "cancelled"
      ? variables.workflowStatus
      : workflow.recommendedStatus;
  const optimisticStatus =
    variables.optimisticStatus
    ?? (variables.workflowStatus === "done" || variables.workflowStatus === "cancelled"
      ? variables.workflowStatus
      : workflow.status);
  const nextPhase = workflowPhaseFromStatus(variables.workflowStatus);

  return {
    ...workflow,
    status: optimisticStatus,
    persistedStatus: variables.workflowStatus,
    recommendedStatus: nextRecommendedStatus,
    phase: nextPhase,
    assignee: nextAssignee,
    updatedAt: new Date().toISOString(),
  };
}

function patchDashboardWorkflow(
  dashboard: DashboardResponse,
  variables: WorkflowMutationInput,
): DashboardResponse {
  return {
    ...dashboard,
    groups: dashboard.groups.map((group) => {
      if (group.fingerprint !== variables.fingerprint) return group;
      return {
        ...group,
        latest: {
          ...group.latest,
          workflowStatus: variables.workflowStatus,
          workflowAssignee: variables.workflowAssignee.trim() || null,
          workflowUpdatedAt: new Date().toISOString(),
        },
        workflow: patchWorkflowSummary(group.workflow, variables),
      };
    }),
  };
}

function patchFindingFamilyWorkflow(
  family: FindingFamilyResponse,
  variables: WorkflowMutationInput,
): FindingFamilyResponse {
  if (family.fingerprint !== variables.fingerprint) return family;

  return {
    ...family,
    latest: {
      ...family.latest,
      workflowStatus: variables.workflowStatus,
      workflowAssignee: variables.workflowAssignee.trim() || null,
      workflowUpdatedAt: new Date().toISOString(),
    },
    workflow: patchWorkflowSummary(family.workflow, variables),
  };
}

export function FindingsPage({ dashboard }: { dashboard: DashboardResponse }) {
  const { client, getFindingFamily, updateFindingFamilyTriage, updateFindingFamilyWorkflow } = useBackendApi();
  const { fingerprint } = useParams<{ fingerprint?: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [consoleState, setConsoleState] = usePersistentState<ThreadConsoleState>(
    "0:threads:console-state",
    DEFAULT_THREAD_CONSOLE_STATE,
  );
  const {
    search,
    workflowFilter,
    reviewFilter,
    severityFilter,
    consensusFilter,
    activeOnly,
    queueSort: storedSort,
  } = consoleState;
  const queueSort = storedSort === "newest" ? "newest" : "severity";
  const [filtersOpen, setFiltersOpen] = useState(false);
  const deferredSearch = useDeferredValue(search);

  const severityOptions = useMemo(
    () => ["all", ...new Set(dashboard.groups.map((group) => group.latest.severity.toLowerCase()))],
    [dashboard.groups],
  );
  const severityCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const group of dashboard.groups) {
      const key = group.latest.severity.toLowerCase();
      counts[key] = (counts[key] ?? 0) + 1;
    }
    return counts;
  }, [dashboard.groups]);

  const filteredGroups = useMemo(() => {
    const normalized = deferredSearch.trim().toLowerCase();

    return dashboard.groups.filter((group) => {
      if (normalized && !matchesSearch(group, normalized)) return false;
      if (workflowFilter !== "all" && group.workflow.phase !== workflowFilter) return false;
      if (reviewFilter !== "all" && group.workflow.reviewGate !== reviewFilter) return false;
      if (severityFilter !== "all" && group.latest.severity.toLowerCase() !== severityFilter) return false;
      if (consensusFilter !== "all" && group.workflow.consensus !== consensusFilter) return false;
      if (activeOnly && group.workflow.activeAgentRoles.length === 0) return false;
      return true;
    });
  }, [activeOnly, consensusFilter, dashboard.groups, deferredSearch, reviewFilter, severityFilter, workflowFilter]);

  const queueGroups = useMemo(() => {
    const ranked = [...filteredGroups];
    ranked.sort((left, right) => {
      if (queueSort === "newest") return right.latest.timestamp - left.latest.timestamp;
      return severityRank(right.latest.severity) - severityRank(left.latest.severity)
        || right.latest.timestamp - left.latest.timestamp;
    });
    return ranked;
  }, [filteredGroups, queueSort]);

  const selectedFingerprint = fingerprint ?? null;

  const familyQuery = useQuery({
    queryKey: ["finding-family", client.backendId, selectedFingerprint],
    queryFn: () => getFindingFamily(selectedFingerprint!),
    enabled: Boolean(selectedFingerprint),
  });

  const triageMutation = useMutation({
    mutationFn: ({
      triageStatus,
      triageNote,
    }: {
      triageStatus: "new" | "accepted" | "suppressed";
      triageNote: string;
    }) => updateFindingFamilyTriage(selectedFingerprint!, triageStatus, triageNote),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["dashboard", client.backendId] }),
        queryClient.invalidateQueries({ queryKey: ["finding-family", client.backendId, selectedFingerprint] }),
      ]);
    },
  });

  const workflowMutation = useMutation({
    mutationFn: ({ fingerprint: familyFingerprint, workflowStatus, workflowAssignee }: WorkflowMutationInput) =>
      updateFindingFamilyWorkflow(familyFingerprint, workflowStatus, workflowAssignee),
    onMutate: async (variables) => {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: ["dashboard", client.backendId] }),
        queryClient.cancelQueries({ queryKey: ["finding-family", client.backendId, variables.fingerprint] }),
      ]);

      const previousDashboard = queryClient.getQueryData<DashboardResponse>(["dashboard", client.backendId]);
      const previousFamily = queryClient.getQueryData<FindingFamilyResponse>(["finding-family", client.backendId, variables.fingerprint]);

      queryClient.setQueryData<DashboardResponse>(["dashboard", client.backendId], (current) =>
        current ? patchDashboardWorkflow(current, variables) : current,
      );
      queryClient.setQueryData<FindingFamilyResponse>(["finding-family", client.backendId, variables.fingerprint], (current) =>
        current ? patchFindingFamilyWorkflow(current, variables) : current,
      );

      return { previousDashboard, previousFamily, fingerprint: variables.fingerprint };
    },
    onError: (_error, _variables, context) => {
      if (context?.previousDashboard) {
        queryClient.setQueryData(["dashboard", client.backendId], context.previousDashboard);
      }
      if (context?.previousFamily) {
        queryClient.setQueryData(["finding-family", client.backendId, context.fingerprint], context.previousFamily);
      }
    },
    onSettled: async (_data, _error, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["dashboard", client.backendId] }),
        queryClient.invalidateQueries({ queryKey: ["finding-family", client.backendId, variables.fingerprint] }),
      ]);
    },
  });

  const selectedGroup = dashboard.groups.find((group) => group.fingerprint === selectedFingerprint) ?? null;
  const filtersActive =
    search.trim().length > 0
    || workflowFilter !== "all"
    || reviewFilter !== "all"
    || severityFilter !== "all"
    || consensusFilter !== "all"
    || activeOnly;
  const activeFilterLabels = [
    workflowFilter !== "all" ? WORKFLOW_ACTIONS.find((action) => action.value === workflowFilter)?.label ?? workflowFilter : null,
    reviewFilter !== "all" ? reviewFilter.replaceAll("_", " ") : null,
    severityFilter !== "all" ? severityFilter : null,
    consensusFilter !== "all" ? consensusFilter.replaceAll("-", " ") : null,
    activeOnly ? "active agents" : null,
  ].filter(Boolean) as string[];

  function clearFilters() {
    setConsoleState((current) => ({
      ...current,
      search: "",
      workflowFilter: "all",
      reviewFilter: "all",
      severityFilter: "all",
      consensusFilter: "all",
      assigneeFilter: "all",
      activeOnly: false,
    }));
  }

  if (selectedFingerprint) {
    return (
      <PageContent width="wide">
        <Button variant="ghost" size="sm" asChild><Link to="/findings"><ArrowLeft className="size-4" />All findings</Link></Button>
        <PageHeader
          title={familyQuery.data?.latest.title ?? selectedGroup?.latest.title ?? "Finding"}
          actions={selectedGroup ? <SubmissionExport findingId={selectedGroup.latest.id} /> : undefined}
        />
        {familyQuery.isLoading ? <LoadingState label="Loading finding" /> : familyQuery.error ? <ErrorState error={familyQuery.error} /> : familyQuery.data ? (
          <FindingFamilyInspector
            key={selectedFingerprint}
            data={familyQuery.data}
            selectedSummary={selectedGroup?.workflow ?? null}
            reviewMode={false}
            isSaving={triageMutation.isPending || workflowMutation.isPending}
            onTriage={(triageStatus, triageNote) => triageMutation.mutate({ triageStatus, triageNote })}
          />
        ) : <EmptyState title="Finding not found" body="It may have been removed." />}
        {triageMutation.error || workflowMutation.error ? <ErrorState error={(triageMutation.error ?? workflowMutation.error)!} /> : null}
      </PageContent>
    );
  }

  return (
    <PageContent width="wide">
      <PageHeader
        eyebrow="Findings"
        title="Findings"
        summary="Assess impact and track remediation."
      />

      <div className="flex flex-wrap items-center gap-2">
        {[
          { key: "critical", label: "Critical", dot: "bg-red-500" },
          { key: "high", label: "High", dot: "bg-orange-500" },
          { key: "medium", label: "Medium", dot: "bg-amber-400" },
          { key: "low", label: "Low", dot: "bg-sky-400" },
          { key: "info", label: "Info", dot: "bg-slate-400" },
        ].map(({ key, label, dot }) => (
          <span key={key} className="inline-flex items-center gap-2 rounded-full border border-border bg-muted/20 px-3 py-1 text-xs">
            <span className={`size-2 rounded-full ${dot}`} />
            <span className="text-muted-foreground">{label}</span>
            <span className="font-mono font-semibold tabular-nums text-foreground">{severityCounts[key] ?? 0}</span>
          </span>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-[var(--radius-panel)] border border-border p-3">
        <span className="text-sm font-medium">All findings <span className="text-muted-foreground">{queueGroups.length}</span></span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
        <Select aria-label="Sort findings" value={queueSort} onValueChange={value => setConsoleState(current => ({ ...current, queueSort: value as QueueSortMode }))} options={[{ value: "severity", label: "Sort: Severity" }, { value: "newest", label: "Sort: Newest" }]} />
        <div className="relative w-full sm:w-60"><Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="Search findings" type="search" autoComplete="off" data-1p-ignore data-lpignore="true" value={search} onChange={event => setConsoleState(current => ({ ...current, search: event.target.value }))} placeholder="Search findings" className="pl-9" /></div>            <Button variant="outline" onClick={() => setFiltersOpen(true)}>
              <SlidersHorizontal className="size-4" />
              Filters
              {filtersActive ? <Badge variant="neutral">{activeFilterLabels.length + (search.trim() ? 1 : 0)}</Badge> : null}
            </Button>
        </div>
      </div>
      {filtersActive ? <div className="space-y-3">

          {filtersActive || search.trim() ? (
            <div className="flex flex-wrap items-center gap-2">
              {search.trim() ? <Badge variant="outline">"{search.trim()}"</Badge> : null}
              {activeFilterLabels.map((label) => (
                <Badge key={label} variant="outline">
                  {label}
                </Badge>
              ))}
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                Clear filters
              </Button>
            </div>
          ) : null}

      </div> : null}

      <div className="min-w-0 space-y-4">
        <ThreadInbox
          groups={queueGroups}
          selectedFingerprint={selectedFingerprint}
          pendingFingerprint={workflowMutation.isPending ? workflowMutation.variables?.fingerprint ?? null : null}
          onSelect={(nextFingerprint) => navigate(`/findings/${nextFingerprint}`)}
        />
      </div>

      <Sheet open={filtersOpen} onOpenChange={setFiltersOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto p-0 sm:max-w-xl">
          <SheetHeader className="border-b border-border pr-12">
            <SheetTitle>Filters</SheetTitle>
          </SheetHeader>

          <div className="space-y-5 p-6">


            <div className="grid gap-4 md:grid-cols-2">
              <FilterRow label="Status">
                <FilterChip active={workflowFilter === "all"} onClick={() => setConsoleState((current) => ({ ...current, workflowFilter: "all" }))}>
                  All
                </FilterChip>
                {WORKFLOW_ACTIONS.map((action) => (
                  <FilterChip
                    key={action.value}
                    active={workflowFilter === action.value}
                    onClick={() => setConsoleState((current) => ({ ...current, workflowFilter: action.value }))}
                  >
                    {action.label}
                  </FilterChip>
                ))}
              </FilterRow>

              <FilterRow label="Review">
                <FilterChip active={reviewFilter === "all"} onClick={() => setConsoleState((current) => ({ ...current, reviewFilter: "all" }))}>
                  All
                </FilterChip>
                <FilterChip active={reviewFilter === "agent_review"} onClick={() => setConsoleState((current) => ({ ...current, reviewFilter: "agent_review" }))}>
                  Agent review
                </FilterChip>
                <FilterChip active={reviewFilter === "human_review"} onClick={() => setConsoleState((current) => ({ ...current, reviewFilter: "human_review" }))}>
                  Human review
                </FilterChip>
              </FilterRow>

              <FilterRow label="Severity">
                {severityOptions.map((option) => (
                  <FilterChip
                    key={option}
                    active={severityFilter === option}
                    onClick={() => setConsoleState((current) => ({ ...current, severityFilter: option }))}
                  >
                    {option === "all" ? "All" : option[0]?.toUpperCase() + option.slice(1)}
                  </FilterChip>
                ))}
              </FilterRow>

              <FilterRow label="Verdict">
                {CONSENSUS_OPTIONS.map((option) => (
                  <FilterChip
                    key={option.value}
                    active={consensusFilter === option.value}
                    onClick={() => setConsoleState((current) => ({ ...current, consensusFilter: option.value }))}
                  >
                    {option.label}
                  </FilterChip>
                ))}
              </FilterRow>


            </div>

            <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
              <div className="text-sm text-muted-foreground">
                {queueGroups.length} of {dashboard.groups.length} shown
              </div>
              <div className="flex items-center gap-2">
                <Button variant="ghost" size="sm" onClick={clearFilters}>
                  Reset
                </Button>
                <Button size="sm" onClick={() => setFiltersOpen(false)}>
                  Apply
                </Button>
              </div>
            </div>
          </div>
        </SheetContent>
      </Sheet>

    </PageContent>
  );
}

function FindingFamilyInspector({
  data,
  selectedSummary,
  reviewMode,
  onTriage,
  isSaving,
}: {
  data: FindingFamilyResponse;
  selectedSummary: FindingWorkflowSummary | null;
  reviewMode: boolean;
  onTriage: (triageStatus: "new" | "accepted" | "suppressed", triageNote: string) => void;
  isSaving: boolean;
}) {
  const [note, setNote] = useState(data.latest.triageNote ?? "");
  const [handoffNotice, setHandoffNotice] = useState<string | null>(null);
  const activeWorkItem = data.workItems.find((item) => item.status === "in_progress") ?? null;
  const blockedWorkItem = data.workItems.find((item) => item.status === "blocked") ?? null;
  const nextQueuedWorkItem = data.workItems.find((item) => item.status === "todo" || item.status === "backlog") ?? null;
  const executionHeadline = blockedWorkItem
    ? "Blocked"
    : activeWorkItem
      ? "Running"
      : nextQueuedWorkItem
        ? "Queued"
        : data.workflow.reviewGate !== "none"
          ? "Waiting for review"
          : "Idle";
  const executionDetail = blockedWorkItem
    ? blockedWorkItem.summary || data.workflow.reviewReason || "Needs your input."
    : activeWorkItem
      ? activeWorkItem.summary || data.workflow.reviewReason || "An agent is working on it."
      : nextQueuedWorkItem
        ? nextQueuedWorkItem.summary || "Next step is ready."
        : data.workflow.reviewReason || "Nothing running.";
  const handoffIntent =
    data.case?.targetType === "repository" && data.workflow.consensus === "verified"
      ? "draft_fix"
      : data.workflow.reviewGate === "human_review"
        ? "verify"
        : "investigate";
  const handoffCommand = data.consoleCommand.replace(
    "--finding-intent investigate",
    `--finding-intent ${handoffIntent}`,
  );
  const handoffTitle =
    handoffIntent === "draft_fix"
      ? "Fix in chat"
      : handoffIntent === "verify"
        ? "Verify in chat"
        : "Investigate in chat";
  const findingLocation = [data.latest.target, data.latest.engagement, data.case?.target, data.latest.category].filter(Boolean).join(" · ");

  const copyHandoff = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard access is unavailable.");
      await navigator.clipboard.writeText(handoffCommand);
      setHandoffNotice("Copied. Paste it in a terminal.");
    } catch {
      setHandoffNotice("Couldn't copy. Select the command and copy it manually.");
    }
  };


  useEffect(() => {
    setNote(data.latest.triageNote ?? "");
  }, [data.fingerprint, data.latest.triageNote]);

  useEffect(() => {
    setHandoffNotice(null);
  }, [data.fingerprint]);


  return (
    <div className="grid min-w-0 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
      <InspectorPane
        className="order-2 min-w-0 rounded-[var(--radius-panel)] border border-border bg-muted/10 xl:col-start-2 xl:row-start-1"
        title={
          reviewMode
            ? "Next step"
            : "Priority"
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <Tooltip content={data.latest.cvssVector || "Technical severity"}><span tabIndex={0} className="flex items-center gap-2 text-xs text-muted-foreground"><ExpIdBadge id={data.latest.expId} /><CvssBadge score={data.latest.cvssScore} severity={data.latest.severity} /></span></Tooltip>
          {data.latest.triageStatus !== "new" ? <StatusBadge value={data.latest.triageStatus} /> : null}
        </div>

        {findingLocation ? <div className="text-sm text-muted-foreground">{findingLocation}</div> : null}

        {blockedWorkItem || data.workflow.phase === "blocked" ? (
          <div className="rounded-[var(--radius-panel,16px)] border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm">
            <span className="font-medium text-foreground">Blocked: </span>
            <span className="text-muted-foreground">{executionDetail}</span>
          </div>
        ) : null}

        <Textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          aria-label="Review note" placeholder="Note (optional)"
        />

        <div className="flex flex-wrap gap-2">
          {data.latest.triageStatus !== "new" ? (
            <Button
              variant="ghost"
              onClick={() => onTriage("new", note)}
              disabled={isSaving}
            >
              <ShieldQuestion className="size-4" />
              Undo
            </Button>
          ) : null}
        </div>



      </InspectorPane>

      <section className="order-1 min-w-0 xl:col-start-1 xl:row-start-1">
        <Tabs defaultValue="overview" className="gap-4">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="occurrences">History ({data.rows.length})</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="space-y-6">
            <section className="space-y-2"><h2 className="text-sm font-medium">Summary</h2><p className="whitespace-pre-wrap text-sm leading-7 text-muted-foreground">{data.latest.description}</p></section>
            {([
              ["Evidence", data.latest.evidenceRequest],
              ["Response", data.latest.evidenceResponse],
              ["Analysis", data.latest.evidenceAnalysis],
            ] as const).filter(([, text]) => Boolean(text?.trim())).map(([label, text]) => (
              <section key={label} className="min-w-0 space-y-3"><h2 className="text-sm font-medium">{label}</h2><pre className="max-h-[36rem] overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-panel)] border border-border bg-muted/20 p-4 text-xs leading-6 text-muted-foreground">{text}</pre></section>
            ))}
          </TabsContent>

          <TabsContent value="occurrences">
            <div className="mb-4 space-y-1 rounded-[var(--radius-panel)] border border-border bg-muted/20 px-4 py-3 text-sm">
              <div><span className="text-muted-foreground">Chat: </span>{data.latest.chatTitle || data.latest.chatId || "unknown"}</div>
              {data.latest.auditPath ? <div className="break-all font-mono text-xs text-muted-foreground">{data.latest.auditPath}</div> : null}
            </div>
            <Card className="overflow-hidden">
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>Scan</TableHead>
                      <TableHead>Time</TableHead>
                      <TableHead className="w-[8rem]">Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.rows.map((row) => (
                      <TableRow key={row.id}>
                        <TableCell className="tabular-nums text-sm text-muted-foreground">{row.scanId.slice(0, 8)}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{formatTime(row.timestamp)}</TableCell>
                        <TableCell><StatusBadge value={row.status} /></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </section>
    </div>
  );
}

function SubmissionExport({ findingId }: { findingId: string }) {
  const api = useBackendApi();
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState("");
  const exportFor = async (platform: string) => {
    setBusy(true);
    setSaved("");
    try {
      const result = await api.webFetchJson<{ path: string }>(
        `/api/findings/${encodeURIComponent(findingId)}/submission-report`,
        { method: "POST", body: JSON.stringify({ platform }) },
      );
      setSaved(result.path);
    } catch (error) {
      setSaved(error instanceof Error ? error.message : "Export failed");
    } finally {
      setBusy(false);
    }
  };
  return <div className="flex min-w-0 items-center gap-2">
    <DropdownMenu>
      <DropdownMenu.Trigger className="flex items-center">
        <Button variant="outline" size="sm" disabled={busy}><Download className="size-4" /> Submission report <ChevronDown className="size-3" /></Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content align="end">
        <DropdownMenu.Group>
          <DropdownMenu.Label>Export to Desktop</DropdownMenu.Label>
          <DropdownMenu.Item onClick={() => void exportFor("intigriti")}>Intigriti</DropdownMenu.Item>
          <DropdownMenu.Item onClick={() => void exportFor("hackerone")}>HackerOne</DropdownMenu.Item>
          <DropdownMenu.Item onClick={() => void exportFor("bugcrowd")}>Bugcrowd</DropdownMenu.Item>
          <DropdownMenu.Item onClick={() => void exportFor("other")}>Other — all three</DropdownMenu.Item>
        </DropdownMenu.Group>
      </DropdownMenu.Content>
    </DropdownMenu>
    {saved ? <span className="max-w-[260px] truncate text-xs text-muted-foreground" title={saved}>{saved.startsWith("/") ? `Saved: ${saved.split("/").pop()}` : saved}</span> : null}
  </div>;
}

function ThreadInbox({
  groups,
  selectedFingerprint,
  pendingFingerprint,
  onSelect,
}: {
  groups: FindingGroup[];
  selectedFingerprint: string | null;
  pendingFingerprint: string | null;
  onSelect: (fingerprint: string) => void;
}) {
  const sections = [{ title: "Findings", entries: groups }];
  return (
    <div className="space-y-5">
      {groups.length === 0 ? <EmptyState title="No findings" /> : null}
      {sections.filter((section) => section.entries.length > 0).map((section) => (
        <section key={section.title} className="space-y-2">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            {section.title}<span className="text-muted-foreground">{section.entries.length}</span>
          </h2>
          <CardList className="overflow-hidden rounded-[var(--radius-panel,16px)]">
            {section.entries.map((group) => (
              <ThreadListItem
                key={group.fingerprint}
                group={group}
                selected={selectedFingerprint === group.fingerprint}
                saving={pendingFingerprint === group.fingerprint}
                mode="inbox"
                onSelect={onSelect}
              />
            ))}
          </CardList>
        </section>
      ))}
    </div>
  );
}

function ThreadReviewDeck({
  agentReviewGroups,
  humanReviewGroups,
  selectedFingerprint,
  pendingFingerprint,
  onSelect,
}: {
  agentReviewGroups: FindingGroup[];
  humanReviewGroups: FindingGroup[];
  selectedFingerprint: string | null;
  pendingFingerprint: string | null;
  onSelect: (fingerprint: string) => void;
}) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card className="overflow-hidden">
        <CardHeader>
          <div>
            <CardTitle className="font-sans text-base font-medium">Agent review</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          {agentReviewGroups.length === 0 ? (
            <div className="px-6 pb-6">
              <EmptyState
                title="Nothing to check"
                body=""
              />
            </div>
          ) : (
            <ScrollArea className="max-h-[70vh]">
              <CardList>
                {agentReviewGroups.map((group) => (
                  <ThreadListItem
                    key={group.fingerprint}
                    group={group}
                    selected={selectedFingerprint === group.fingerprint}
                    saving={pendingFingerprint === group.fingerprint}
                    mode="review"
                    onSelect={onSelect}
                  />
                ))}
              </CardList>
            </ScrollArea>
          )}
        </CardContent>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader>
          <div>
            <CardTitle className="font-sans text-base font-medium">Your review</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          {humanReviewGroups.length === 0 ? (
            <div className="px-6 pb-6">
              <EmptyState
                title="Nothing to review"
                body=""
              />
            </div>
          ) : (
            <ScrollArea className="max-h-[70vh]">
              <CardList>
                {humanReviewGroups.map((group) => (
                  <ThreadListItem
                    key={group.fingerprint}
                    group={group}
                    selected={selectedFingerprint === group.fingerprint}
                    saving={pendingFingerprint === group.fingerprint}
                    mode="review"
                    onSelect={onSelect}
                  />
                ))}
              </CardList>
            </ScrollArea>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function ThreadListItem({
  group,
  selected,
  saving,
  mode,
  onSelect,
}: {
  group: FindingGroup;
  selected: boolean;
  saving: boolean;
  mode: "inbox" | "review";
  onSelect: (fingerprint: string) => void;
}) {
  const helperText = group.workflow.reviewReason;

  return (
    <CardListItem interactive selected={selected} className="p-0">
      <Link
        to={`/findings/${encodeURIComponent(group.fingerprint)}`}
        onClick={event => { if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); onSelect(group.fingerprint); } }}
        className="group/finding grid w-full min-w-0 gap-3 rounded-[var(--radius-control)] px-4 py-5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:grid-cols-[minmax(0,1fr)_180px_140px] sm:gap-6"
      >
        <div className="min-w-0 space-y-2">
          <div className="flex items-start gap-2"><ExpIdBadge id={group.latest.expId} /><span className="text-sm font-medium leading-6 text-foreground">{group.latest.title}</span><ArrowUpRight aria-hidden="true" className="mt-1 size-3.5 shrink-0 text-muted-foreground opacity-0 group-hover/finding:opacity-100" /></div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">{(group.latest.target || group.latest.engagement) ? <span className="rounded-full bg-muted/40 px-2 py-0.5">{group.latest.target || group.latest.engagement}</span> : null}<span>{group.count} occurrence{group.count === 1 ? "" : "s"}</span></div>
          {helperText ? <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">{helperText}</p> : null}
        </div>
        <div className="flex flex-wrap items-start gap-2 sm:flex-col sm:justify-center">
          <CvssBadge score={group.latest.cvssScore} severity={group.latest.severity} />
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:flex-col sm:items-end sm:justify-center">
          {group.latest.triageStatus !== "new" ? <StatusBadge value={group.latest.triageStatus} /> : null}
          <span className="text-xs tabular-nums text-muted-foreground">{saving ? "Saving…" : formatTime(group.workflow.updatedAt ?? group.latest.timestamp)}</span>
        </div>
      </Link>
    </CardListItem>
  );
}

function TaskStatusBadge({
  value,
}: {
  value: "backlog" | "todo" | "in_progress" | "blocked" | "done" | "cancelled";
}) {
  const variant =
    value === "done"
      ? "success"
      : value === "blocked" || value === "cancelled"
        ? "danger"
        : value === "in_progress"
          ? "accent"
          : value === "todo"
            ? "warning"
            : "neutral";

  return <Badge variant={variant}>{value.replaceAll("_", " ")}</Badge>;
}

function ExecutionGraph({
  items,
  activeWorkItemId,
  blockedWorkItemId,
}: {
  items: FindingFamilyResponse["workItems"];
  activeWorkItemId: string | null;
  blockedWorkItemId: string | null;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {items.map((item) => {
        const isCurrent = activeWorkItemId === item.id;
        const isBlocked = blockedWorkItemId === item.id;
        const toneClass = isBlocked
          ? "border-destructive/30 bg-destructive/5"
          : isCurrent
            ? "border-primary/30 bg-primary/5"
            : "border-border bg-card";

        return (
          <div key={item.id} className={`rounded-[var(--radius-panel,16px)] border border-transparent px-4 py-3 ${toneClass}`}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="text-sm font-medium text-foreground">{item.title}</div>
                  {isCurrent ? <Badge variant="accent">Current</Badge> : null}
                  {isBlocked ? <Badge variant="danger">Blocked</Badge> : null}
                </div>
                <div className="mt-1 text-xs leading-5 text-muted-foreground">{item.summary}</div>
              </div>
              <TaskStatusBadge value={item.status} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SummaryMetric({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-[var(--radius-panel,16px)] bg-muted/30 px-4 py-3">
      <div className="text-sm text-muted-foreground/70">{label}</div>
      <div className={mono ? "mt-2 truncate tabular-nums text-sm text-foreground" : "mt-2 text-sm font-medium text-foreground"}>
        {value}
      </div>
    </div>
  );
}

function formatArtifactKindLabel(kind: FindingFamilyResponse["artifacts"][number]["kind"]) {
  return kind.replaceAll("_", " ");
}

function formatCaseTargetTypeLabel(value: NonNullable<FindingFamilyResponse["case"]>["targetType"]) {
  return value.replaceAll("-", " ");
}

function severityRank(severity: string) {
  const normalized = severity.toLowerCase();
  if (normalized === "critical") return 5;
  if (normalized === "high") return 4;
  if (normalized === "medium") return 3;
  if (normalized === "low") return 2;
  return 1;
}

function workflowPhaseFromStatus(status: FindingWorkflowStatus): FindingWorkflowPhase {
  if (status === "done" || status === "cancelled" || status === "blocked" || status === "in_progress" || status === "todo") {
    return status;
  }
  return "backlog";
}

function FilterRow({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset className={`min-w-0 rounded-[var(--radius-panel,16px)] bg-muted/30 px-4 py-4 ${className ?? ""}`}>
      <legend className="float-left w-full text-sm font-medium">{label}</legend>
      <div className="clear-both flex flex-wrap gap-1.5 pt-3">{children}</div>
    </fieldset>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      aria-pressed={active}
      variant={active ? "default" : "outline"}
      size="xs"
      onClick={onClick}
    >
      {children}
    </Button>
  );
}
