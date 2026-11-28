/**
 * The rest of the board: the coordinator, the fleet's settings, the mesh, this machine, and the
 * background jobs started from this console process.
 */
import { useState, type ReactNode } from "react";
import { Megaphone, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Disclosure } from "@/components/ui/disclosure";
import { Table, TableBody, TableHeader, TableRow } from "@/components/ui/table";
import { useRun, type OpsOverview } from "./api";
import { Dot, EmptyRow, Meter, Mono, Panel, RelTime, StateBadge, Td, Th, fmtTime } from "./ui";

export function CoordinatorPanel({ overview }: { overview: OpsOverview }) {
  const coordinator = overview.coordinator;
  const { run, pending } = useRun();
  return (
    <Panel
      title="Coordinator"
      description="One process that hosts the mesh bus, the swarm database, the health loop and the autopilot."
      actions={!coordinator.running && (
        <Button type="button" size="sm" variant="accent" disabled={pending} onClick={() => void run("/api/ops/coordinator/start", {}, { ok: "Coordinator started" })}>
          <Play className="size-3.5" />Start coordinator
        </Button>
      )}
    >
      <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-6 gap-y-2 text-sm">
        <dt className="text-muted-foreground">State</dt>
        <dd data-coordinator-state={coordinator.running ? "running" : "stopped"}>
          <StateBadge tone={coordinator.running ? "emerald" : "neutral"} pulse={coordinator.running}>{coordinator.running ? "Running" : "Not running"}</StateBadge>
        </dd>
        <dt className="text-muted-foreground">Mesh bus</dt>
        <dd>{coordinator.bus ? <Mono>{coordinator.bus}</Mono> : <span className="text-muted-foreground">no broker — nothing is listening</span>}</dd>
      </dl>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">
        Chats do not need it. Engagements do: their agents register on the bus and take work from the queue.
        It is started on the first engagement, or from here.
      </p>
      {coordinator.log.length > 0 && (
        <Disclosure
          className="mt-3"
          summary={`Log — last ${coordinator.log.length} lines${coordinator.log_updated ? `, ${fmtTime(coordinator.log_updated)}` : ""}`}
          triggerClassName="py-1 text-xs text-muted-foreground"
        >
          <pre data-coordinator-log="" className="max-h-56 overflow-auto rounded-[var(--radius-control,8px)] bg-muted p-3 font-mono text-[12px] leading-5 text-foreground">{coordinator.log.join("\n")}</pre>
        </Disclosure>
      )}
    </Panel>
  );
}

function Fact({ name, children }: { name: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{name}</dt>
      <dd className="min-w-0 break-words text-foreground">{children}</dd>
    </>
  );
}

export function FleetPanel({ overview }: { overview: OpsOverview }) {
  const fleet = overview.fleet;
  return (
    <Panel title="Fleet settings" description="What governs every agent at once.">
      <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-6 gap-y-2.5 text-sm">
        <Fact name="Pause flag">
          <span className="inline-flex flex-wrap items-center gap-2">
            <StateBadge tone={fleet.pause_flag ? "amber" : "neutral"}>{fleet.pause_flag ? "Set — the fleet holds" : "Clear"}</StateBadge>
            <span className="text-xs text-muted-foreground">each agent finishes its current step, then waits</span>
          </span>
        </Fact>
        <Fact name="Autopilot">
          <span className="inline-flex flex-wrap items-center gap-2">
            <StateBadge tone={fleet.autopilot.enabled ? (fleet.autopilot.blocked ? "amber" : "emerald") : "neutral"}>
              {!fleet.autopilot.enabled ? "Off" : fleet.autopilot.blocked ? "Held" : "Armed"}
            </StateBadge>
            {fleet.autopilot.enabled && fleet.autopilot.blocked && <span className="text-xs text-muted-foreground">{fleet.autopilot.note}</span>}
            {fleet.autopilot.enabled && !fleet.autopilot.blocked && <span className="text-xs text-muted-foreground">re-queues a quiet swarm after an hour idle</span>}
          </span>
        </Fact>
        <Fact name="Turn cap">{fleet.turn_cap ? `${fleet.turn_cap} turns per intent` : <span className="text-muted-foreground">unlimited</span>}</Fact>
        <Fact name="Leader"><Mono>{fleet.models.leader || "—"}</Mono></Fact>
        <Fact name="Supervisor"><Mono>{fleet.models.supervisor || "—"}</Mono></Fact>
        <Fact name="Worker"><Mono>{fleet.models.worker || "—"}</Mono></Fact>
        <Fact name="Planner route"><Mono>{fleet.models.planner || "the leader"}</Mono></Fact>
        <Fact name="Operator mode"><span className="capitalize">{fleet.operator_mode}</span></Fact>
      </dl>
    </Panel>
  );
}

export function MeshPanel({ overview }: { overview: OpsOverview }) {
  const peers = overview.mesh.peers;
  const chat = overview.mesh.chat.slice(0, 30);
  const [saying, setSaying] = useState("");
  const { run, pending } = useRun();
  return (
    <Panel title="Mesh" description="The shared channel: which agents are present, what was said on it, and a way to say something yourself." flush>
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border/60 px-4 py-2.5 text-xs text-muted-foreground">
        <span className="tabular-nums">{peers.length}</span> present
        {peers.slice(0, 12).map((peer, index) => (
          <Badge key={index} variant="neutral" className="max-w-[14rem] font-normal"><span className="truncate font-mono">{String(peer.identity ?? peer.id ?? peer.name ?? peer.pid ?? "peer")}</span></Badge>
        ))}
      </div>
      <form
        className="flex flex-wrap items-center gap-2 border-b border-border/60 px-4 py-2.5"
        onSubmit={(event) => {
          event.preventDefault();
          if (saying.trim()) void run("/api/say", { msg: saying.trim() }, { ok: "Said on the mesh" }).then(() => setSaying(""));
        }}
      >
        <Input value={saying} onChange={(event) => setSaying(event.target.value)} placeholder="Say something every agent reads at its next step" aria-label="Say on the mesh" className="min-w-[16rem] flex-1" maxLength={400} />
        <Button type="submit" size="sm" variant="secondary" disabled={pending || !saying.trim()}><Megaphone className="size-3.5" />Say</Button>
      </form>
      <Table>
        <TableHeader><TableRow><Th>When</Th><Th>Who</Th><Th>Message</Th></TableRow></TableHeader>
        <TableBody>
          {!chat.length && <EmptyRow colSpan={3}>Nothing on the mesh yet.</EmptyRow>}
          {chat.map((line, index) => (
            <TableRow key={`${line.ts}-${index}`} data-mesh-line="">
              <Td className="whitespace-nowrap text-muted-foreground">{line.ts}</Td>
              <Td><Mono className="text-muted-foreground">{line.src}</Mono></Td>
              <Td className="max-w-[40rem]"><div className="truncate" title={line.message}>{line.message}</div></Td>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Panel>
  );
}

/** The M1 / M2 / M3 hierarchy an engagement was started with, and who in it still has a heartbeat. */
export function StructuredPanel({ overview }: { overview: OpsOverview }) {
  const teams = overview.structured ?? [];
  const alive = (id: string) => Boolean(overview.agents.find((agent) => agent.id === id)?.alive);
  const member = (id: string, role = "") => (
    <span className="inline-flex items-center gap-1.5">
      <Dot tone={alive(id) ? "emerald" : "neutral"} />
      {role && <span className="text-xs text-muted-foreground">{role}</span>}
      <Mono className="text-muted-foreground">{id}</Mono>
    </span>
  );
  return (
    <Panel title="Structured teams" description="Leader, supervisors and workers started together for an engagement. A grey member has no heartbeat." flush>
      <Table>
        <TableHeader><TableRow><Th>Engagement</Th><Th>Leader</Th><Th>Supervisors</Th><Th>Workers</Th><Th>Alive</Th><Th>Registered</Th></TableRow></TableHeader>
        <TableBody>
          {!teams.length && <EmptyRow colSpan={6}>No structured team on record.</EmptyRow>}
          {teams.map((team) => (
            <TableRow key={team.file} data-team={team.slug}>
              <Td className="max-w-[16rem]"><div className="truncate" title={team.slug}>{team.slug}</div></Td>
              <Td>{team.leader ? member(team.leader) : <span className="text-muted-foreground">—</span>}</Td>
              <Td><div className="flex flex-col gap-0.5">{team.supervisors.map((m) => <span key={m.id}>{member(m.id, m.role)}</span>)}</div>{!team.supervisors.length && <span className="text-muted-foreground">—</span>}</Td>
              <Td>
                <div className="flex flex-col gap-0.5">{team.workers.slice(0, 12).map((m) => <span key={m.id}>{member(m.id, m.role)}</span>)}</div>
                {team.workers.length > 12 && <span className="text-xs text-muted-foreground">+{team.workers.length - 12} more</span>}
                {!team.workers.length && <span className="text-muted-foreground">—</span>}
              </Td>
              <Td className="tabular-nums">{team.live} / {1 + team.supervisors.length + team.workers.length}</Td>
              <Td className="whitespace-nowrap text-muted-foreground">{team.updated.slice(0, 16).replace("T", " ")}</Td>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Panel>
  );
}

const num = (value: unknown): number => (typeof value === "number" ? value : Number(value) || 0);

export function SystemPanel({ overview }: { overview: OpsOverview }) {
  const system = overview.system;
  const cores = Array.isArray(system.cpu_per_core) ? (system.cpu_per_core as number[]) : [];
  // macOS lists every sealed system volume; the disks that matter are the root, the data volume and externals.
  const mountsAll = Array.isArray(system.mounts) ? (system.mounts as { mount: string; used_gb: number; total_gb: number; pct: number }[]) : [];
  const mounts = mountsAll.filter((mount) => mount.mount === "/" || mount.mount.endsWith("/Data") || mount.mount.startsWith("/Volumes/")).slice(0, 4);
  const memUsed = num(system.mem_used_gb), memTotal = num(system.mem_total_gb);
  const swapUsed = num(system.swap_used_gb), swapTotal = num(system.swap_total_gb);
  return (
    <Panel title="This machine" description={overview.browser.running ? `Browser daemon running on port ${overview.browser.port}.` : "Measured now. The browser daemon is not running; it starts when a tool needs it."}>
      <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
        <div className="space-y-2">
          <div className="flex items-baseline justify-between text-sm"><span className="text-muted-foreground">CPU</span><span className="tabular-nums">{num(system.cpu_percent).toFixed(0)}%</span></div>
          <Meter value={num(system.cpu_percent)} max={100} tone={num(system.cpu_percent) > 85 ? "amber" : "sky"} />
          {cores.length > 0 && (
            <div className="flex h-6 items-end gap-0.5" aria-hidden="true">
              {cores.map((load, index) => <div key={index} className="w-1.5 rounded-sm bg-sky-500/70" style={{ height: `${Math.max(6, Math.min(100, load))}%` }} />)}
            </div>
          )}
          {Array.isArray(system.load_avg) && <div className="text-xs text-muted-foreground">load {(system.load_avg as number[]).join(" · ")}</div>}
        </div>
        <div className="space-y-2">
          <div className="flex items-baseline justify-between text-sm"><span className="text-muted-foreground">Memory</span><span className="tabular-nums">{memUsed.toFixed(1)} / {memTotal.toFixed(1)} GB</span></div>
          <Meter value={memUsed} max={memTotal} tone={memTotal && memUsed / memTotal > 0.85 ? "amber" : "violet"} />
          <div className="text-xs text-muted-foreground">swap {swapUsed.toFixed(1)} / {swapTotal.toFixed(1)} GB</div>
        </div>
        <div className="space-y-1 text-sm">
          <div className="text-muted-foreground">Disk</div>
          <div className="tabular-nums">↓ {num(system.disk_read_mbs).toFixed(1)} MB/s · ↑ {num(system.disk_write_mbs).toFixed(1)} MB/s · {num(system.disk_iops)} iops</div>
          {mountsAll.length > mounts.length && <div className="text-xs text-muted-foreground">{mountsAll.length - mounts.length} system volumes not shown</div>}
        </div>
        <div className="space-y-1 text-sm">
          <div className="text-muted-foreground">Network</div>
          <div className="tabular-nums">↓ {num(system.net_rx_mbs).toFixed(1)} MB/s · ↑ {num(system.net_tx_mbs).toFixed(1)} MB/s</div>
        </div>
      </div>
      {mounts.length > 0 && (
        <div className="mt-4 space-y-1.5">
          {mounts.map((mount, index) => (
            <div key={index} className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 text-xs">
              <Mono className="truncate text-muted-foreground">{mount.mount}</Mono>
              <Meter value={num(mount.pct)} max={100} tone={num(mount.pct) > 90 ? "amber" : "neutral"} />
              <span className="tabular-nums text-muted-foreground">{num(mount.used_gb).toFixed(0)} / {num(mount.total_gb).toFixed(0)} GB · {num(mount.pct).toFixed(0)}%</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

export function JobsPanel({ overview }: { overview: OpsOverview }) {
  const jobs = overview.jobs;
  return (
    <Panel title="Background jobs" description="Long tool runs started from this console process. The registry lives in the process that started the job." flush>
      <Table>
        <TableHeader><TableRow><Th>Job</Th><Th>Tool</Th><Th>State</Th><Th>Started</Th><Th>Detail</Th></TableRow></TableHeader>
        <TableBody>
          {!jobs.length && <EmptyRow colSpan={5}>No background job in this process.</EmptyRow>}
          {jobs.map((job) => {
            const status = String(job.status ?? "?");
            const tone = status === "running" ? "sky" : status === "done" ? "emerald" : status === "failed" ? "red" : "neutral";
            return (
              <TableRow key={String(job.job_id)}>
                <Td><Mono>{String(job.job_id)}</Mono></Td>
                <Td>{String(job.tool_name ?? "—")}</Td>
                <Td><StateBadge tone={tone}>{status}</StateBadge></Td>
                <Td><RelTime epoch={typeof job.started_at === "number" ? job.started_at : null} /></Td>
                <Td className="max-w-[24rem]"><div className="truncate text-muted-foreground" title={String(job.error ?? job.label ?? "")}>{String(job.error ?? job.label ?? "")}</div></Td>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Panel>
  );
}
