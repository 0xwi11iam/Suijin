import { SourceChip } from "@/components/ui/activity-chip";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Markdown } from "./markdown";
import { ChevronRight, FileSearch, FileText, Globe, Search, Terminal, Wrench, ListChecks, PencilLine, Users, MessageSquare, Clock, Database } from "lucide-react";
import { ActivityRow } from "./activity-row";
import { ActivityIndicator, ActivityMark } from "./loading-state";
import { presentToolCall } from "./tool-presentation";
import { toolRowTitle, filePathFromCall, exploitBoxFromResult, type ExploitBox } from "./tool-row";
import { toolLabels, type ToolLabels } from "./tool-labels";
import { toolCallStatus } from "./tool-call-status";
import type { ToolCallState } from "./transcript";
import "./tool-activity.css";
import { Disclosure } from "@/components/ui/disclosure";
import { JsonView } from "@/console/json-view";

function toolKind(name: string) {
  if (/todo|plan|checklist/i.test(name)) return { Icon: ListChecks, label: "Plan" };
  if (/spawn|delegate|agent.*create/i.test(name)) return { Icon: Users, label: "Agents" };
  if (/message|send.*agent/i.test(name)) return { Icon: MessageSquare, label: "Messages" };
  if (/browser|web|fetch|http/i.test(name)) return { Icon: Globe, label: "Web" };
  if (/search|grep|find/i.test(name)) return { Icon: Search, label: "Search" };
  if (/bash|shell|exec|terminal|command/i.test(name)) return { Icon: Terminal, label: "Commands" };
  if (/write|edit|patch/i.test(name)) return { Icon: PencilLine, label: "Edits" };
  if (/read|file/i.test(name)) return { Icon: FileText, label: "Files" };
  if (/review|scan|analy/i.test(name)) return { Icon: FileSearch, label: "Review" };
  if (/wait|sleep/i.test(name)) return { Icon: Clock, label: "Waiting" };
  if (/memory|store|database/i.test(name)) return { Icon: Database, label: "Memory" };
  return { Icon: Wrench, label: "Tools" };
}
function ToolIcon({ name }: { name: string }) {
  const { Icon } = toolKind(name);
  return <Icon aria-hidden="true" className="size-3.5" />;
}

// Compatibility export: semantic details and raw payloads share one safe formatter.
export { toolPresentationText as toolPayloadText } from "./tool-presentation";

function toolTitle(call: ToolCallState, labels: ToolLabels): string {
  return toolRowTitle(
    { name: call.name, arguments: call.arguments, result: call.result, isRunning: call.isRunning, status: presentToolCall(call).status },
    labels,
  );
}

const SEVERITY_CLASS: Record<string, string> = {
  CRITICAL: "border-destructive/60 text-destructive",
  HIGH: "border-orange-400/50 text-orange-300",
  MEDIUM: "border-amber-400/50 text-amber-300",
  LOW: "border-sky-400/50 text-sky-300",
  INFO: "border-border text-muted-foreground",
};

/** The one card a CONFIRMED exploit earns (operator ruling 2026-11-28): verdict, id,
 *  severity, title, asset — no YAML pane, no buttons. Everything else stays JSON. */
function ExploitCard({ box, running }: { box: ExploitBox; running: boolean }) {
  return <div data-exploit-box="" className="space-y-1.5 rounded-lg border border-border bg-muted/30 px-3.5 py-3">
    <div className="flex items-center gap-2 text-[11px] font-medium tracking-wide">
      <span className={`rounded border px-1.5 py-0.5 ${SEVERITY_CLASS[box.severity] ?? SEVERITY_CLASS.INFO}`}>{box.severity}</span>
      <span className="font-mono text-muted-foreground">{box.expId} · CVSS {box.cvss}</span>
      {running && <span className="text-muted-foreground/70">· running…</span>}
    </div>
    <p className="text-sm font-medium leading-5 text-foreground">{box.title}</p>
    {box.target && <p className="font-mono text-[11px] text-muted-foreground">{box.target}</p>}
  </div>;
}

/** File tools open to the path, nothing else (operator ruling 2026-11-28). */
function FilePathBody({ path, running }: { path: string; running: boolean }) {
  return <div data-file-path="" className="rounded-lg border border-border bg-muted/30 px-3.5 py-2.5">
    <p className="truncate font-mono text-xs leading-5 text-foreground" title={path}>{path}</p>
    {running && <p className="pt-0.5 text-[11px] text-muted-foreground">working…</p>}
  </div>;
}

function ToolDetails({ call }: { call: ToolCallState }) {
  const box = exploitBoxFromResult(call.result);
  if (box) return <ExploitCard box={box} running={call.isRunning} />;
  const path = filePathFromCall(call);
  if (path) return <FilePathBody path={path} running={call.isRunning} />;
  const view = presentToolCall(call);
  return <div className="space-y-3">
    {/* The bordered box holds a shell command or a tool's source. An ordinary tool's output is the
        JSON tree below — an empty "Output" box above it was just noise (seen 2026-11-28). */}
    {(view.command || view.source) && <div className="overflow-hidden rounded-lg border border-border bg-muted/30 text-xs">
      <div className="px-3 py-2 text-muted-foreground">{view.command ? "Shell" : view.language || "Output"}</div>
      <div className="max-h-80 overflow-auto px-3 pb-3 font-mono text-xs leading-6">
        {view.command && <pre aria-label={`${call.name} command`} className="mb-3 whitespace-pre-wrap break-words">$ {view.command}</pre>}
        {view.source && view.language && <pre aria-label={`${call.name} source`} className="mb-3 whitespace-pre-wrap break-words">{view.source}</pre>}
        {view.command && view.output !== undefined && <pre aria-label={`${call.name} output`} className="whitespace-pre-wrap break-words">{view.output || (call.isRunning ? "Waiting for output…" : "No output.")}</pre>}
        {call.isRunning && view.output === undefined && <span className="text-muted-foreground">Waiting for output…</span>}
      </div>
    </div>}
    {/* A tool's own output is JSON as often as not: show it as a readable tree, never a wall of text. */}
    {view.output !== undefined && !view.command && <JsonView key={`${call.id}-output`} label={`${call.name} output`} text={view.output || (call.isRunning ? "Waiting for output…" : "No output.")} maxHeight={360} />}
    {view.input && !view.command && !view.source && <JsonView key={`${call.id}-input`} label={`${call.name} input`} text={view.input} maxHeight={256} />}
    {view.metadata.length > 0 && <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">{view.metadata.map(item => <span key={item.label}>{item.label}: {item.value}</span>)}</div>}
    <Disclosure summary="Raw details" triggerClassName="py-1 text-[11px] text-muted-foreground">
      <div className="space-y-3 pt-2">{view.rawArguments && <JsonView text={view.rawArguments} maxHeight={256} />}{view.rawResult && <JsonView text={view.rawResult} maxHeight={360} />}</div>
    </Disclosure>
  </div>;
}

function ToolResult({ call, labels }: { call: ToolCallState; labels: ToolLabels }) {
  const status = presentToolCall(call).status;
  return <ActivityRow icon={call.isRunning ? <ActivityMark compact /> : <ToolIcon name={call.name} />} title={toolTitle(call, labels)} status={status === "Error" || status === "Stopped" ? status : ""} running={call.isRunning} failed={status === "Error"}><ToolDetails call={call} /></ActivityRow>;
}

/** How far from the end (px) still counts as "at the end". A few pixels, no more: one line is 24px,
 *  and a reader who scrolls back by a line has scrolled back (a 24px tolerance pulled them straight
 *  back down — measured 2026-11-28). */
const FOLLOW_SLACK = 4;

/**
 * The model's reasoning, in light grey, following its own stream: the newest line stays in view
 * while it arrives, and the moment the reader scrolls back the stream waits for them (operator
 * 2026-11-28: "make the reasoning stream auto scroll as fast as it streams and user can scroll
 * back if needed"). A finished block opens at its beginning, like any other text.
 */
function ReasoningStream({ text, live }: { text: string; live: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  useLayoutEffect(() => {
    const element = box.current;
    if (element && live && following.current) element.scrollTop = element.scrollHeight;
  }, [text, live]);
  return (
    <div
      ref={box}
      data-reasoning=""
      data-following={live && following.current ? "" : undefined}
      onScroll={event => {
        const element = event.currentTarget;
        following.current = element.scrollHeight - element.scrollTop - element.clientHeight < FOLLOW_SLACK;
      }}
      className="console-tool-reasoning max-h-48 overflow-y-auto overscroll-contain break-words text-xs leading-6 text-muted-foreground"
    >
      <Markdown text={text} muted />
    </div>
  );
}

/** One activity row per batch combines live status, latest operation and history. */
export function ToolActivity({ calls, reasoning, working = false, showLatest = false }: { calls: ToolCallState[]; reasoning?: string; working?: boolean; showLatest?: boolean; startedAt?: string; endedAt?: string }) {
  const [expanded, setExpanded] = useState(false);
  const [labels, setLabels] = useState<ToolLabels>({});
  useEffect(() => { let live = true; toolLabels().then(map => { if (live) setLabels(map); }); return () => { live = false; }; }, []);
  if (!calls.length && !reasoning) return null;
  const running = calls.filter(call => call.isRunning);
  const failed = calls.filter(call => toolCallStatus(call) === "Error").length;
  const active = running.length > 0 || working;
  const sources = recordedSources(calls.filter(call => toolCallStatus(call) === "Done"));
  const latest = showLatest || active ? running.at(-1) ?? calls.at(-1) : undefined;
  const history = calls.filter(call => call !== latest);
  const GroupIcon = toolKind(latest?.name ?? calls[0]?.name ?? "tools").Icon;
  return <section className="console-tool-activity" aria-label="Tool activity" data-active={active || undefined} data-latest-tool={latest ? "true" : undefined}>
    {/* ACTIVE BATCHES STAY OPEN (field 2026-11-17: streaming re-mounts reset
      the disclosure state — "Thinking…" collapsed instantly and the operator
      could never read the reasoning. An active batch shows its reasoning and
      tool details live; finished batches keep the manual toggle.) */}
    <Disclosure open={expanded || active} onOpenChange={setExpanded} triggerClassName="group/tool-history inline-flex w-full min-w-0 items-center gap-2 rounded-[var(--radius-control)] py-1 text-left text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring" summary={<>
      {active ? <ActivityMark compact /> : <GroupIcon aria-hidden="true" className="size-3.5 shrink-0" />}
      {active && <span className="console-working-label shrink-0">{calls.length ? "Working…" : "Thinking…"}</span>}
      {latest ? <span className="min-w-0 truncate" title={toolTitle(latest, labels)}>{active ? `· ${latest.isRunning ? presentToolCall(latest).title : toolTitle(latest, labels)}` : toolTitle(latest, labels)}</span> : !active && <span>{calls.length ? `${calls.length} tool call${calls.length === 1 ? "" : "s"}` : "Thinking"}</span>}
      {latest && calls.length > 1 && <span className="shrink-0 text-muted-foreground/70">· {calls.length} tool calls</span>}
      {!latest && running.length > 0 && <span role="status">· {running.length} running</span>}
      {sources.length > 0 && <span className="shrink-0 text-muted-foreground/70">· {sources.length} {sources.length === 1 ? "source" : "sources"}</span>}
      {failed > 0 && <span className="shrink-0 text-destructive">· {failed} failed</span>}
      <ChevronRight aria-hidden="true" className={`console-history-chevron size-3 shrink-0 transition-transform ${expanded ? "rotate-90 opacity-100" : "opacity-0 group-hover/tool-history:opacity-100 group-focus-visible/tool-history:opacity-100 [@media(hover:none)]:opacity-100"}`} />
    </>}>
      <div className="space-y-3 py-2">
        {sources.length > 0 && <div aria-label="Sources" className="flex flex-wrap gap-2">{sources.map(source => <SourceChip key={source} href={source} target="_blank" rel="noopener noreferrer" icon={<Globe aria-hidden="true" />}>{new URL(source).hostname}</SourceChip>)}</div>}
        {reasoning && <ReasoningStream text={reasoning} live={active} />}
        {latest && <ToolDetails call={latest} />}
        {history.length > 0 && <div className="space-y-0.5">{latest && <p className="pb-1 text-[11px] text-muted-foreground">Earlier calls</p>}{history.map(call => <ToolResult key={call.id} call={call} labels={labels} />)}</div>}
      </div>
    </Disclosure>
  </section>;
}

/** Only recorded URL evidence becomes a source chip; never manufacture search counts. */
function recordedSources(calls: ToolCallState[]): string[] {
  const sources = new Set<string>();
  for (const call of calls) {
    if (toolKind(call.name).label !== "Web" && !/search/i.test(call.name)) continue;
    let text: string;
    try { text = JSON.stringify([call.arguments, call.result]).slice(0, 100_000); } catch { continue; }
    for (const match of text.matchAll(/https?:\/\/[^\s"<>\\]+/g)) {
      try { const url = new URL(match[0]); if (url.hostname) sources.add(url.origin); } catch { /* Ignore malformed tool output. */ }
    }
  }
  return [...sources];
}
