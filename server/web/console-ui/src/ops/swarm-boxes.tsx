/**
 * Boxes and the warm pile: the container engine, the sandbox template image, the persistent
 * Suijin box, every sandbox an agent runs in, and the pile of pre-created ones held ready.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Hammer, RefreshCw, ScrollText, Square, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip } from "@/components/ui/tooltip";
import { Disclosure } from "@/components/ui/disclosure";
import { Table, TableBody, TableHeader, TableRow } from "@/components/ui/table";
import { useOps, useRun, type OpsBoxes, type OpsSandbox, type OpsOverview } from "./api";
import { ConfirmDialog, EmptyRow, ErrorNote, Meter, Mono, Panel, StateBadge, Stat, Td, Th } from "./ui";

function EngineBanner({ boxes }: { boxes: OpsBoxes }) {
  if (boxes.loading) return <p className="text-xs text-muted-foreground">Checking the container engine…</p>;
  if (boxes.error) return <ErrorNote error={boxes.error} />;
  if (boxes.engine !== "running") {
    return (
      <p data-boxes-engine="off" className="text-sm leading-6 text-muted-foreground">
        No container engine is answering. Boxed agents need OrbStack or Docker; Suijin never starts one on its own.
        Host agents, chats and engagements work without it.
      </p>
    );
  }
  return <p className="text-xs text-muted-foreground">Engine answering. State refreshes every few seconds{boxes.took_ms ? ` (last check ${boxes.took_ms} ms)` : ""}.</p>;
}

function ImageCard({ boxes, build }: { boxes: OpsBoxes; build: OpsOverview["image_build"] }) {
  const { run, pending } = useRun();
  const image = boxes.image ?? { name: "suijin/sandbox-base", tag: "", exists: false, fresh: null };
  const [rebuild, setRebuild] = useState(false);
  // With no engine running nothing looks at images: the console says so instead of "not built".
  const engineOff = boxes.engine !== "running";
  const tone = engineOff || !image.exists ? "neutral" : image.fresh === false ? "amber" : image.fresh ? "emerald" : "neutral";
  const label = engineOff ? "not checked" : !image.exists ? "not built" : image.fresh === false ? "stale" : image.fresh ? "fresh" : "built";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm text-foreground">Sandbox image</div>
          <Mono className="block truncate text-muted-foreground">{image.tag || image.name}</Mono>
        </div>
        <StateBadge tone={tone}>{label}</StateBadge>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">
        {engineOff
          ? "Every sandbox starts from this image. With no engine running the console does not look at images; start OrbStack or Docker to see it and build it."
          : "Every sandbox starts from this image. It is rebuilt once per Suijin change; running agents are untouched."}
      </p>
      <div className="flex flex-wrap gap-2">
        {boxes.engine === "running" && (
          <>
            <Button type="button" size="sm" variant="secondary" disabled={pending || build.state === "running"} onClick={() => void run("/api/ops/boxes/image-build", {}, { ok: "Image build started" })}>
              <Hammer className="size-3.5" />{image.exists ? "Build if stale" : "Build image"}
            </Button>
            <Button type="button" size="sm" variant="ghost" disabled={pending || build.state === "running"} onClick={() => setRebuild(true)}>Rebuild from scratch</Button>
          </>
        )}
      </div>
      {build.state !== "idle" && (
        <Disclosure summary={build.state === "running" ? "Building — live log" : build.state === "failed" ? "Last build failed" : "Last build"} defaultOpen={build.state === "running"}>
          {build.error && <ErrorNote error={build.error} />}
          <pre data-image-build-log="" className="max-h-56 overflow-auto rounded-[var(--radius-control,8px)] bg-muted p-3 font-mono text-[12px] leading-5 text-foreground">{build.log.join("\n") || "…"}</pre>
        </Disclosure>
      )}
      <ConfirmDialog open={rebuild} onOpenChange={setRebuild} title="Rebuild the image from scratch?" tone="warning"
        description="A clean build takes a while. Running sandboxes keep the old image; new ones get the new one." confirmLabel="Rebuild" pending={pending}
        onConfirm={() => { void run("/api/ops/boxes/image-build", { force: true }, { ok: "Rebuild started" }).then(() => setRebuild(false)); }} />
    </div>
  );
}

function SandboxesTable({ boxes }: { boxes: OpsBoxes }) {
  const { run, pending } = useRun();
  const [logs, setLogs] = useState<OpsSandbox | null>(null);
  const [stop, setStop] = useState<OpsSandbox | null>(null);
  const ops = useOps();
  const rows = boxes.sandboxes ?? [];
  return (
    <>
      <Table>
        <TableHeader><TableRow><Th>Sandbox</Th><Th>Engagement</Th><Th>Agent</Th><Th>State</Th><Th>Age</Th><Th className="text-right">Actions</Th></TableRow></TableHeader>
        <TableBody>
          {!rows.length && <EmptyRow colSpan={6}>No agent sandbox is running.</EmptyRow>}
          {rows.map((box) => (
            <TableRow key={box.name} data-sandbox={box.name}>
              <Td className="max-w-[22rem]"><div className="truncate font-mono text-[12px]" title={box.name}>{box.name.replace(/^suijin-sbx-/, "")}</div></Td>
              <Td><Mono className="text-muted-foreground">{box.engagement || "—"}</Mono></Td>
              <Td><Mono className="text-muted-foreground">{box.identity || "—"}</Mono></Td>
              <Td><StateBadge tone={box.running ? "emerald" : "neutral"}>{box.status || (box.running ? "up" : "stopped")}</StateBadge></Td>
              <Td className="text-muted-foreground">{box.age || "—"}</Td>
              <Td>
                <div className="flex items-center justify-end gap-0.5">
                  <Tooltip content="Logs"><Button type="button" variant="ghost" size="icon-xs" aria-label={`Logs of ${box.name}`} onClick={() => setLogs(box)}><ScrollText className="size-3.5" /></Button></Tooltip>
                  <Tooltip content="Stop and remove"><Button type="button" variant="ghost" size="icon-xs" aria-label={`Stop ${box.name}`} onClick={() => setStop(box)}><Square className="size-3.5" /></Button></Tooltip>
                </div>
              </Td>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <ConfirmDialog open={Boolean(logs)} onOpenChange={(open) => !open && setLogs(null)} title={`${logs?.name ?? ""} — logs`} confirmLabel="Close" onConfirm={() => setLogs(null)}>
        <pre data-sandbox-logs="" className="mt-3 max-h-[480px] overflow-auto rounded-[var(--radius-control,8px)] bg-muted p-3 font-mono text-[12px] leading-5 text-foreground">
          {logs ? <SandboxLogs name={logs.name} /> : null}
        </pre>
      </ConfirmDialog>
      <ConfirmDialog open={Boolean(stop)} onOpenChange={(open) => !open && setStop(null)} title="Stop this sandbox?" tone="warning"
        description={<span>The agent inside exits. Its engagement data lives on this machine and is untouched. <Mono>{stop?.name}</Mono></span>}
        confirmLabel="Stop" pending={pending}
        onConfirm={() => { if (stop) void run(`/api/ops/boxes/sandboxes/${encodeURIComponent(stop.name)}/stop`, {}, { ok: "Sandbox removed" }).then(() => setStop(null)); }} />
    </>
  );
}

function SandboxLogs({ name }: { name: string }) {
  const ops = useOps();
  const log = useQuery({
    queryKey: ["ops", "sandbox-log", name],
    queryFn: ({ signal }) => ops.get<{ lines: string[] }>(`/api/ops/boxes/sandboxes/${encodeURIComponent(name)}/logs`, undefined, signal),
    refetchInterval: 3000,
  });
  if (log.isPending) return <span className="text-muted-foreground">Reading logs…</span>;
  if (log.error) return <span className="text-destructive">{log.error.message}</span>;
  return <>{log.data.lines.length ? log.data.lines.join("\n") : "No output yet."}</>;
}

function PileCard({ boxes, overview }: { boxes: OpsBoxes; overview: OpsOverview }) {
  const { run, pending } = useRun();
  const pool = boxes.pool ?? { size: 0, target: 0 };
  const [count, setCount] = useState("4");
  const [gc, setGc] = useState(false);
  const asked = Math.max(0, Math.min(32, Number(count) || 0));
  const engineOff = boxes.engine !== "running";
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <div className="text-sm text-foreground">Warm pile</div>
        <div className="text-2xl font-medium tabular-nums tracking-tight text-foreground">{pool.size}<span className="text-sm font-normal text-muted-foreground"> / {pool.target || "no target"}</span></div>
      </div>
      <Meter value={pool.size} max={Math.max(pool.target, pool.size, 1)} tone={pool.size ? "sky" : "neutral"} label={`${pool.size} boxes ready`} />
      <p className="text-xs leading-5 text-muted-foreground">
        Pre-created sandboxes that were never started, so the next boxed agent begins from a container that already exists.
        They hold no agent and run nothing. The target is <Mono>sandbox_pool_size</Mono> (now {overview.fleet.pool_target}).
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Input type="number" min={0} max={32} value={count} onChange={(event) => setCount(event.target.value)} aria-label="Warm pile size" className="w-20 tabular-nums" disabled={engineOff} />
        <Button type="button" size="sm" variant="secondary" disabled={engineOff || pending} onClick={() => void run("/api/ops/boxes/summon", { n: asked }, { ok: `Warm pile set to ${asked}` })}>Summon to {asked}</Button>
        <Button type="button" size="sm" variant="ghost" disabled={engineOff || pending || !pool.size} onClick={() => void run("/api/ops/boxes/destroy-idle", {}, { ok: "Idle boxes destroyed" })}>
          <Trash2 className="size-3.5" />Destroy idle
        </Button>
        <Button type="button" size="sm" variant="ghost" className="text-destructive" disabled={engineOff || pending || (!pool.size && !(boxes.sandboxes ?? []).length)} onClick={() => setGc(true)}>Remove all sandboxes</Button>
      </div>
      {pool.size > 0 && (
        <ul className="space-y-1" data-pile-members="">
          {(pool.members ?? []).map((member) => (
            <li key={member.name} className="flex items-center justify-between gap-3 text-xs">
              <Mono className="truncate text-muted-foreground">{member.name}</Mono>
              <Badge variant="neutral" className="font-normal">ready</Badge>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog open={gc} onOpenChange={setGc} title="Remove every sandbox?" tone="danger" typed="remove"
        description="Agent sandboxes and the warm pile go. Agents inside exit; engagement data lives on this machine and is untouched."
        confirmLabel="Remove all" pending={pending}
        onConfirm={() => { void run("/api/ops/boxes/gc", {}, { ok: "Sandboxes removed" }).then(() => setGc(false)); }} />
    </div>
  );
}

export function BoxesPanel({ overview }: { overview: OpsOverview }) {
  const boxes = overview.boxes;
  const { run, pending } = useRun();
  const box = boxes.box ?? { engine: "", name: "suijin-box", adopted: false, state: "unknown" };
  const running = (boxes.sandboxes ?? []).filter((sandbox) => sandbox.running).length;
  return (
    <Panel
      title="Boxes and warm pile"
      description={<>Sandboxes are the disposable containers boxed agents run in; the warm pile is a reserve of them, created and never started.</>}
      actions={<Button type="button" variant="ghost" size="icon-sm" aria-label="Refresh box state" disabled={pending} onClick={() => void run("/api/ops/boxes/refresh", {}, { ok: "Box state refreshed" })}><RefreshCw className="size-4" /></Button>}
    >
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Stat label="Engine" tone={boxes.engine === "running" ? "emerald" : "neutral"} value={boxes.loading ? "checking" : boxes.engine === "running" ? "running" : "not running"} hint={boxes.loading ? "looking, without waking anything" : boxes.engine_socket ? "socket present" : "OrbStack or Docker"} />
        <Stat label="Agent sandboxes" tone={running ? "sky" : "neutral"} value={running} hint={`${(boxes.sandboxes ?? []).length - running} stopped`} />
        <Stat label="Warm pile" tone={(boxes.pool?.size ?? 0) ? "sky" : "neutral"} value={boxes.pool?.size ?? 0} hint={`target ${boxes.pool?.target ?? 0}`} />
        <Stat label="Suijin box" tone={box.state === "running" ? "emerald" : "neutral"} value={boxes.loading ? "checking" : box.state} hint={box.note ?? `${box.name}${box.adopted ? " · adopted" : ""}${box.engine ? ` · ${box.engine}` : ""}`} />
      </div>
      <div className="mt-3"><EngineBanner boxes={boxes} /></div>
      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        <div className="rounded-[var(--radius-control,8px)] border border-border/60 p-3.5"><PileCard boxes={boxes} overview={overview} /></div>
        <div className="rounded-[var(--radius-control,8px)] border border-border/60 p-3.5"><ImageCard boxes={boxes} build={overview.image_build} /></div>
        <div className="rounded-[var(--radius-control,8px)] border border-border/60 p-3.5 text-sm leading-6 text-muted-foreground">
          <div className="text-foreground">What a sandbox is</div>
          A sealed Linux container with Suijin and its tools. It sees the config read-only and shares the swarm database and engagement folders.
          It is created with <Mono>--rm</Mono>, so it removes itself when its agent exits. Three mounts, nothing else from this machine.
        </div>
      </div>
      <div className="mt-4 overflow-hidden rounded-[var(--radius-control,8px)] border border-border/60">
        <SandboxesTable boxes={boxes} />
      </div>
    </Panel>
  );
}
