/**
 * Every engagement and chat, and what each one holds: its journal, its files, its exploits.
 */
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, Braces, FolderOpen } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableHeader, TableRow } from "@/components/ui/table";
import { Markdown } from "@/console/markdown";
import { JsonView } from "@/console/json-view";
import { CvssBadge, ExpIdBadge, SeverityBadge } from "@/components/status-badges";
import { useEngagementEvents, useEngagementFile, useEngagements, useEngagement, type EngagementDetail, type EngagementRow } from "./api";
import { EmptyRow, ErrorNote, JsonDialog, Meter, Mono, Panel, RelTime, StateBadge, Stat, Td, Th, Tone, fmtBytes, fmtTime } from "./ui";

export const KIND_TONE: Record<string, Tone> = { engagement: "sky", chat: "violet", legacy: "neutral" };

export function EngagementList({ onOpen }: { onOpen?: (id: string) => void }) {
  const query = useEngagements();
  const navigate = useNavigate();
  if (query.error) return <ErrorNote error={query.error} />;
  const rows = query.data?.engagements ?? [];
  const open = (id: string) => (onOpen ? onOpen(id) : navigate(`/engagements/${encodeURIComponent(id)}`));
  return (
    <Panel title="Engagements and chats" description={rows.length ? `${rows.length} on this machine, newest activity first.` : "Nothing has run yet."} flush>
      <Table>
        <TableHeader>
          <TableRow>
            <Th>Name</Th><Th>Kind</Th><Th>Target</Th><Th>Mode</Th><Th>Agents</Th><Th>Events</Th><Th>Findings</Th><Th>Exploits</Th><Th>Size</Th><Th>Last activity</Th>
          </TableRow>
        </TableHeader>
        <TableBody>
          {!rows.length && <EmptyRow colSpan={10}>No engagement folder yet. Start one from <Link to="/chats?new=1&kind=engagement" className="text-primary-text underline-offset-4 hover:underline">New engagement</Link>.</EmptyRow>}
          {rows.map((row: EngagementRow) => (
            <TableRow key={row.id} data-engagement-id={row.id} className="cursor-pointer" onClick={() => open(row.id)}>
              <Td className="max-w-[22rem]">
                <button type="button" className="block max-w-full text-left" onClick={(event) => { event.stopPropagation(); open(row.id); }}>
                  <span className="block truncate text-foreground">{row.name || row.id}</span>
                  <Mono className="block truncate text-muted-foreground">{row.id}</Mono>
                </button>
              </Td>
              <Td><StateBadge tone={KIND_TONE[row.kind] ?? "neutral"}>{row.kind}</StateBadge></Td>
              <Td className="max-w-[14rem]"><div className="truncate font-mono text-[12px]" title={row.target}>{row.target || <span className="text-muted-foreground">—</span>}</div></Td>
              <Td className="text-muted-foreground">{[row.mode, row.permissions].filter(Boolean).join(" · ") || "—"}</Td>
              <Td className="tabular-nums">{row.live_agents ? <span className="text-emerald-600 dark:text-emerald-300">{row.live_agents}</span> : <span className="text-muted-foreground">0</span>}</Td>
              <Td className="tabular-nums">{row.events.toLocaleString()}</Td>
              <Td className="tabular-nums">{row.findings}</Td>
              <Td className="tabular-nums">{row.exploits}</Td>
              <Td className="tabular-nums text-muted-foreground">{fmtBytes(row.bytes)}</Td>
              <Td><RelTime epoch={row.lastActivity ? Date.parse(row.lastActivity) / 1000 : null} /></Td>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Panel>
  );
}

function what(record: Record<string, unknown>): string {
  const kind = String(record.kind ?? "");
  if (kind === "tool.call") {
    const args = record.args;
    return `${String(record.name ?? "tool")} ${typeof args === "object" && args ? JSON.stringify(args).slice(0, 110) : ""}`;
  }
  if (kind === "tool.result") return `${record.ok === false ? "failed" : "returned"} ${String(record.output ?? "").slice(0, 110)}`;
  if (kind === "assistant.message" || kind === "assistant.reasoning" || kind === "user.message") return String(record.content ?? "").slice(0, 140);
  return JSON.stringify(record).slice(0, 140);
}

const EVENT_TONE: Record<string, Tone> = {
  "tool.call": "sky", "tool.result": "neutral", "assistant.message": "violet", "assistant.reasoning": "violet", "user.message": "amber",
  finding: "red", "session.start": "emerald", "session.complete": "emerald", "session.interrupt": "red", error: "red",
};

function EventsTab({ id, kinds }: { id: string; kinds: { kind: string; rows: number }[] }) {
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<Record<string, unknown> | null>(null);
  const events = useEngagementEvents(id, kind, offset, q);
  const total = events.data?.total ?? 0;
  const rows = events.data?.rows ?? [];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Record kind">
          <Button type="button" size="xs" variant={kind === "" ? "default" : "secondary"} onClick={() => { setKind(""); setOffset(0); }}>all</Button>
          {kinds.map((entry) => (
            <Button key={entry.kind} type="button" size="xs" variant={kind === entry.kind ? "default" : "secondary"} onClick={() => { setKind(entry.kind); setOffset(0); }}>
              {entry.kind}<span className="ml-1.5 tabular-nums opacity-70">{entry.rows}</span>
            </Button>
          ))}
        </div>
        <Input value={q} onChange={(event) => { setQ(event.target.value); setOffset(0); }} placeholder="Search the journal" aria-label="Search events" className="ml-auto w-64" />
      </div>
      {events.error && <ErrorNote error={events.error} />}
      <div className="overflow-hidden rounded-[var(--radius-control,8px)] border border-border/60">
        <Table>
          <TableHeader><TableRow><Th>Seq</Th><Th>When</Th><Th>Kind</Th><Th>Agent</Th><Th>What</Th></TableRow></TableHeader>
          <TableBody>
            {!rows.length && <EmptyRow colSpan={5}>{events.isPending ? "Reading…" : "No record of that kind."}</EmptyRow>}
            {rows.map((record, index) => (
              <TableRow key={`${String(record.seq)}-${index}`} className="cursor-pointer" onClick={() => setOpen(record)}>
                <Td className="tabular-nums text-muted-foreground">{String(record.seq ?? "·")}</Td>
                <Td className="whitespace-nowrap text-muted-foreground">{fmtTime(String(record.ts ?? ""))}</Td>
                <Td><StateBadge tone={EVENT_TONE[String(record.kind)] ?? "neutral"}>{String(record.kind ?? "?")}</StateBadge></Td>
                <Td><Mono className="text-muted-foreground">{String(record.agent ?? "")}</Mono></Td>
                <Td className="max-w-[36rem]"><div className="truncate font-mono text-[12px]" title={what(record)}>{what(record)}</div></Td>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{total ? `${offset + 1}–${Math.min(offset + rows.length, total)} of ${total}` : ""}</span>
        <div className="flex gap-2">
          <Button type="button" size="xs" variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Newer</Button>
          <Button type="button" size="xs" variant="secondary" disabled={offset + 100 >= total} onClick={() => setOffset(offset + 100)}>Older</Button>
        </div>
      </div>
      <JsonDialog open={Boolean(open)} onOpenChange={(next) => !next && setOpen(null)} title={`Record ${String(open?.seq ?? "")} · ${String(open?.kind ?? "")}`} value={open} description={fmtTime(String(open?.ts ?? ""))} />
    </div>
  );
}

function groupFiles(files: { path: string; bytes: number; modified: string }[]) {
  const groups = new Map<string, typeof files>();
  for (const file of files) {
    if (file.path === "events.jsonl") continue;      // the journal has its own tab
    const top = file.path.includes("/") ? file.path.split("/")[0] : "loose files";
    const bucket = groups.get(top) ?? [];
    bucket.push(file);
    groups.set(top, bucket);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

function FilePreview({ id, path }: { id: string; path: string | null }) {
  const file = useEngagementFile(id, path);
  if (!path) return <div className="flex h-full min-h-[16rem] items-center justify-center text-sm text-muted-foreground">Choose a file to read it here.</div>;
  if (file.isPending) return <div className="p-4 text-sm text-muted-foreground">Reading…</div>;
  if (file.error) return <div className="p-4"><ErrorNote error={file.error} /></div>;
  const row = file.data?.rows?.[0];
  if (!row) return <div className="p-4 text-sm text-muted-foreground">Nothing came back.</div>;
  if (row.binary) return <div className="p-4 text-sm text-muted-foreground">A binary file ({fmtBytes(row.bytes)}). Its contents are not shown.</div>;
  const content = row.content ?? "";
  return (
    <div className="flex h-full min-h-[20rem] min-w-0 flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-border/60 px-3 py-2 text-xs text-muted-foreground">
        <Mono className="truncate">{row.name}</Mono>
        <span className="shrink-0 tabular-nums">{fmtBytes(row.bytes)}{row.truncated ? " · first part shown" : ""}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {/\.md$/i.test(row.name) ? <div className="console-markdown text-sm"><Markdown text={content} /></div>
          : /\.json$/i.test(row.name) ? <JsonView text={content} maxHeight={600} />
          : /\.jsonl$/i.test(row.name) ? <pre className="whitespace-pre-wrap break-all font-mono text-[12px] leading-5">{content}</pre>
          : <pre className="whitespace-pre-wrap break-all font-mono text-[12px] leading-5">{content}</pre>}
      </div>
    </div>
  );
}

function FilesTab({ detail }: { detail: EngagementDetail }) {
  const groups = useMemo(() => groupFiles(detail.files), [detail.files]);
  const [path, setPath] = useState<string | null>(null);
  return (
    <div className="grid min-h-[24rem] overflow-hidden rounded-[var(--radius-control,8px)] border border-border/60 md:grid-cols-[18rem_1fr]">
      <div className="max-h-[36rem] overflow-auto border-b border-border/60 md:border-b-0 md:border-r">
        {groups.map(([top, files]) => (
          <div key={top}>
            <div className="sticky top-0 bg-card px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{top} <span className="tabular-nums">· {files.length}</span></div>
            {files.map((file) => (
              <button key={file.path} type="button" onClick={() => setPath(file.path)} aria-current={path === file.path}
                className={`flex w-full items-baseline gap-2 px-3 py-1.5 text-left font-mono text-[12px] hover:bg-[var(--surface-hover)] ${path === file.path ? "bg-[var(--surface-selected)] text-foreground" : "text-muted-foreground"}`}>
                <span className="min-w-0 flex-1 truncate">{file.path.includes("/") ? file.path.slice(top.length + 1) : file.path}</span>
                <span className="shrink-0 tabular-nums opacity-70">{fmtBytes(file.bytes)}</span>
              </button>
            ))}
          </div>
        ))}
        {!groups.length && <div className="px-3 py-8 text-center text-sm text-muted-foreground">No file was written.</div>}
      </div>
      <FilePreview id={detail.id} path={path} />
    </div>
  );
}

function ExploitsTab({ detail }: { detail: EngagementDetail }) {
  const [catalog, setCatalog] = useState(false);
  const file = useEngagementFile(detail.id, catalog ? "exploits/catalog.json" : null);
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">Catalogued exploits: one YAML and one call each, verified against the target's real output.</p>
        <Button type="button" size="xs" variant="secondary" onClick={() => setCatalog(true)}><Braces className="size-3" />catalog.json</Button>
      </div>
      <div className="overflow-hidden rounded-[var(--radius-control,8px)] border border-border/60">
        <Table>
          <TableHeader><TableRow><Th>Exploit</Th><Th>Title</Th><Th>Class</Th><Th>Severity</Th><Th>CVSS</Th><Th>State</Th><Th>Runs</Th><Th>Last verdict</Th></TableRow></TableHeader>
          <TableBody>
            {!detail.exploits.length && <EmptyRow colSpan={8}>No exploit catalogued.</EmptyRow>}
            {detail.exploits.map((exploit) => (
              <TableRow key={exploit.id}>
                <Td><Mono>{exploit.id}</Mono></Td>
                <Td className="max-w-[26rem]"><div className="truncate" title={exploit.title ?? ""}>{exploit.title}</div></Td>
                <Td className="text-muted-foreground">{exploit.class || "—"}</Td>
                <Td><SeverityBadge severity={exploit.severity || "info"} /></Td>
                <Td className="tabular-nums">{exploit.cvss ?? "—"}</Td>
                <Td><StateBadge tone={exploit.status === "confirmed" ? "emerald" : "neutral"}>{exploit.status || "—"}</StateBadge></Td>
                <Td className="tabular-nums">{exploit.attempts}</Td>
                <Td><Mono>{exploit.last_verdict || "—"}</Mono></Td>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <JsonDialog open={catalog} onOpenChange={setCatalog} title="exploits/catalog.json" text={file.data?.rows?.[0]?.content} />
    </div>
  );
}

function OverviewTab({ detail }: { detail: EngagementDetail }) {
  const kinds = Object.entries(detail.events.kinds).sort((a, b) => b[1] - a[1]);
  const agents = Object.entries(detail.events.agents).sort((a, b) => b[1] - a[1]);
  const maxKind = kinds[0]?.[1] ?? 1;
  const [meta, setMeta] = useState(false);
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
        <Stat label="Journal" value={detail.events.lines.toLocaleString()} hint="records" tone="sky" />
        <Stat label="Findings" value={detail.findings.length} tone={detail.findings.length ? "emerald" : "neutral"} />
        <Stat label="Exploits" value={detail.exploits.length} tone={detail.exploits.length ? "sky" : "neutral"} />
        <Stat label="Files" value={detail.files.length} />
        <Stat label="Size" value={fmtBytes(detail.files.reduce((sum, file) => sum + file.bytes, 0))} hint="excluding the agent's home" />
      </div>
      <div className="grid gap-6 xl:grid-cols-2">
        <Panel title="What happened" description="Records in the journal, by kind.">
          <div className="space-y-2">
            {kinds.map(([kind, count]) => (
              <div key={kind} className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3 text-xs">
                <StateBadge tone={EVENT_TONE[kind] ?? "neutral"}>{kind}</StateBadge>
                <Meter value={count} max={maxKind} tone={EVENT_TONE[kind] === "red" ? "red" : EVENT_TONE[kind] === "violet" ? "violet" : "sky"} />
                <span className="w-12 text-right tabular-nums text-muted-foreground">{count.toLocaleString()}</span>
              </div>
            ))}
            {!kinds.length && <p className="text-sm text-muted-foreground">The journal is empty.</p>}
          </div>
        </Panel>
        <Panel title="Agents" description="Who wrote the journal." flush>
          <Table>
            <TableBody>
              {!agents.length && <EmptyRow colSpan={2}>{detail.kind === "chat" ? "Suijin itself — a chat is a conversation, not a hunt." : "No agent on record."}</EmptyRow>}
              {agents.map(([agent, count]) => (
                <TableRow key={agent}><Td><Mono>{agent}</Mono></Td><Td className="text-right tabular-nums text-muted-foreground">{count.toLocaleString()}</Td></TableRow>
              ))}
            </TableBody>
          </Table>
        </Panel>
      </div>
      <Panel title="Findings" description="Catalogued from this engagement." flush>
        <Table>
          <TableHeader><TableRow><Th>Severity</Th><Th>Finding</Th><Th>State</Th></TableRow></TableHeader>
          <TableBody>
            {!detail.findings.length && <EmptyRow colSpan={3}>No finding recorded.</EmptyRow>}
            {detail.findings.map((finding) => (
              <TableRow key={finding.id}>
                <Td>
                  <span className="inline-flex items-center gap-1.5">
                    <SeverityBadge severity={finding.severity || "info"} />
                    <ExpIdBadge id={finding.exp_id} />
                    <CvssBadge score={finding.cvss} severity={finding.severity} />
                  </span>
                </Td>
                <Td className="max-w-[40rem]"><Link to="/findings" className="truncate text-foreground hover:underline">{finding.title}</Link></Td>
                <Td><StateBadge tone={finding.status === "confirmed" ? "emerald" : "neutral"}>{finding.status}</StateBadge></Td>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Panel>
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span>Inbox: {detail.inbox.length ? detail.inbox.map((file) => file.path).join(", ") : "empty"}</span>
        <Button type="button" size="xs" variant="ghost" onClick={() => setMeta(true)}><Braces className="size-3" />engagement.json</Button>
      </div>
      <JsonDialog open={meta} onOpenChange={setMeta} title="engagement.json" value={detail.meta} />
    </div>
  );
}

export function EngagementDetailPanel({ id }: { id: string }) {
  const query = useEngagement(id);
  const events = useEngagementEvents(id, "", 0, "");     // the journal's kinds, kept fresh
  const navigate = useNavigate();
  if (query.error) return <ErrorNote error={query.error}>{String(query.error.message)} — <button type="button" className="underline" onClick={() => navigate("/engagements")}>back to all engagements</button></ErrorNote>;
  const detail = query.data;
  if (!detail) return <Panel><div className="py-8 text-center text-sm text-muted-foreground">Reading…</div></Panel>;
  const kinds = events.data?.kinds ?? Object.entries(detail.events.kinds).map(([kind, rows]) => ({ kind, rows }));
  const session = detail.kind === "chat" || detail.kind === "engagement";
  return (
    <>
      <Button asChild variant="ghost" size="sm" className="-ml-2"><Link to="/engagements"><ArrowLeft className="size-4" />All engagements</Link></Button>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="min-w-0 break-words text-2xl font-medium tracking-tight text-foreground">{String(detail.meta.name ?? detail.id)}</h1>
            <StateBadge tone={KIND_TONE[detail.kind] ?? "neutral"}>{detail.kind}</StateBadge>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <Mono>{detail.id}</Mono>
            {String(detail.meta.target ?? "") && <Mono>→ {String(detail.meta.target)}</Mono>}
            {detail.kind !== "chat" && <span>{[String(detail.meta.mode ?? ""), String(detail.meta.permissions ?? "")].filter(Boolean).join(" · ")}</span>}
            <span>{fmtTime(String(detail.meta.createdAt ?? ""))}</span>
          </div>
        </div>
        {session && <Button asChild variant="accent" size="sm"><Link to={`/chats/${encodeURIComponent(detail.id)}`}><FolderOpen className="size-3.5" />Open chat</Link></Button>}
      </div>
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="events">Journal <span className="tabular-nums opacity-70">{detail.events.lines.toLocaleString()}</span></TabsTrigger>
          <TabsTrigger value="files">Files <span className="tabular-nums opacity-70">{detail.files.length}</span></TabsTrigger>
          <TabsTrigger value="exploits">Exploits <span className="tabular-nums opacity-70">{detail.exploits.length}</span></TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="pt-4"><OverviewTab detail={detail} /></TabsContent>
        <TabsContent value="events" className="pt-4"><EventsTab id={detail.id} kinds={kinds} /></TabsContent>
        <TabsContent value="files" className="pt-4"><FilesTab detail={detail} /></TabsContent>
        <TabsContent value="exploits" className="pt-4"><ExploitsTab detail={detail} /></TabsContent>
      </Tabs>
    </>
  );
}
