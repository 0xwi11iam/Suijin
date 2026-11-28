/** Swarms as things (the operator's 2026-11-28 plan): the list, the define dialog, and the
 *  headless detail — every control scoped to one swarm, never machine-wide by accident. */
import { useState } from "react";
import { FolderPlus, Layers, Pause, Play, Send, Square, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ConfirmDialog, EmptyRow, ErrorNote, FlashBar, Mono, Panel, RelTime, StateBadge, Td, Th, type Tone } from "@/ops/ui";
import { useRun, useSwarm, useSwarmActivity, useSwarms, type OpsSwarm } from "@/ops/api";

export function swarmTone(status: string): Tone {
  if (status === "running") return "emerald";
  if (status === "paused") return "amber";
  if (status === "defining") return "sky";
  if (status === "stopped") return "violet";
  return "neutral";
}

export function parseLanes(text: string): string[] {
  return text.split("\n").map(line => line.trim()).filter(Boolean).slice(0, 16);
}

const ENGINES = ["auto", "orbstack", "docker", "podman"] as const;
const PERMISSIONS = ["recon", "exploitation", "maximum"] as const;
const MODES = ["BBP", "VDP", "CTF", "pentest"] as const;

export function DefineSwarmDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { run, pending } = useRun();
  const [target, setTarget] = useState("");
  const [title, setTitle] = useState("");
  const [lanes, setLanes] = useState("");
  const [host, setHost] = useState("4");
  const [boxed, setBoxed] = useState("0");
  const [engine, setEngine] = useState<(typeof ENGINES)[number]>("auto");
  const [permissions, setPermissions] = useState<(typeof PERMISSIONS)[number]>("exploitation");
  const [mode, setMode] = useState<(typeof MODES)[number]>("BBP");
  const selectClass = "h-8 rounded-lg border border-border bg-background px-2 text-xs";
  const launch = async () => {
    const result = await run<{ ok?: boolean; swarm?: OpsSwarm }>("/api/ops/swarms", {
      target, title, lanes: parseLanes(lanes),
      host: Number(host) || 0, boxed: Number(boxed) || 0,
      engine, permissions, mode, start: true,
    }, { ok: `Swarm defined and launched — folder Targets/${target}/swarms` });
    if (result?.ok) { onOpenChange(false); setTarget(""); setTitle(""); setLanes(""); }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Define a swarm</DialogTitle>
          <DialogDescription>
            One folder per swarm under <Mono>Targets/&lt;target&gt;/swarms/</Mono> — the definition is the truth: mission, lanes, agents, brain, engine.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="block space-y-1"><span className="text-xs text-muted-foreground">Target</span>
              <Input value={target} onChange={e => setTarget(e.target.value)} placeholder="immutable" className="h-8 text-sm" /></label>
            <label className="block space-y-1"><span className="text-xs text-muted-foreground">Engine</span>
              <select className={selectClass} value={engine} onChange={e => setEngine(e.target.value as typeof engine)}>
                {ENGINES.map(v => <option key={v} value={v}>{v}</option>)}
              </select></label>
          </div>
          <label className="block space-y-1"><span className="text-xs text-muted-foreground">Mission (the standing title)</span>
            <Input value={title} onChange={e => setTitle(e.target.value)} placeholder="Hunt the SSO hub" className="h-8 text-sm" /></label>
          <label className="block space-y-1"><span className="text-xs text-muted-foreground">Lanes — one per line; queued as prioritised work for this swarm only</span>
            <Textarea value={lanes} onChange={e => setLanes(e.target.value)} rows={3} className="text-sm" placeholder={"map the edge, then the auth flows\nprobe every API path"} /></label>
          <div className="grid grid-cols-4 items-end gap-3">
            <label className="block space-y-1"><span className="text-xs text-muted-foreground">Host agents</span>
              <Input type="number" min={0} max={8} value={host} onChange={e => setHost(e.target.value)} className="h-8 text-sm" /></label>
            <label className="block space-y-1"><span className="text-xs text-muted-foreground">Boxed agents</span>
              <Input type="number" min={0} max={16} value={boxed} onChange={e => setBoxed(e.target.value)} className="h-8 text-sm" /></label>
            <label className="block space-y-1"><span className="text-xs text-muted-foreground">Permissions</span>
              <select className={selectClass} value={permissions} onChange={e => setPermissions(e.target.value as typeof permissions)}>
                {PERMISSIONS.map(v => <option key={v} value={v}>{v}</option>)}
              </select></label>
            <label className="block space-y-1"><span className="text-xs text-muted-foreground">Mode</span>
              <select className={selectClass} value={mode} onChange={e => setMode(e.target.value as typeof mode)}>
                {MODES.map(v => <option key={v} value={v}>{v}</option>)}
              </select></label>
          </div>
          <p className="text-xs text-muted-foreground">Launching starts the coordinator if needed, queues the lanes, then spawns the agents — capacity is checked and refused honestly.</p>
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" size="sm" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="button" size="sm" disabled={pending || !target.trim() || !title.trim()} onClick={() => void launch()}>
            <FolderPlus className="size-3.5" />Define and launch
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The headless view: what the swarm is, who is in it, what work is open — and its controls. */
export function SwarmDetailDialog({ swarmId, onClose }: { swarmId: string | undefined; onClose: () => void }) {
  const query = useSwarm(swarmId);
  const { run, pending } = useRun();
  const [steerOpen, setSteerOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const swarm = query.data?.swarm;
  const agents = query.data?.agents ?? [];
  const intents = query.data?.open_intents ?? [];
  const act = (action: string) => void run(`/api/ops/swarm/${action}`, { swarm: swarmId }, { ok: `${action} → ${swarmId}` });
  return (
    <Dialog open={Boolean(swarmId)} onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="truncate">{swarm ? swarm.title : swarmId}</DialogTitle>
          <DialogDescription className="flex flex-wrap items-center gap-2">
            {swarm && <StateBadge tone={swarmTone(swarm.status)}>{swarm.status}</StateBadge>}
            {swarm && <Mono>{swarm.target}</Mono>}
            {swarm?.created && <span>· created <RelTime epoch={Math.floor((Date.parse(swarm.created) || 0) / 1000) || undefined} /></span>}
            {swarm?.engine && <span>· engine <Mono>{swarm.engine}</Mono></span>}
            {swarm?.permissions && <span>· {swarm.permissions}</span>}
            {swarm?.mode && <span>{swarm.mode}</span>}
          </DialogDescription>
        </DialogHeader>
        {query.error && <ErrorNote error="the swarm did not answer" />}
        <div className="max-h-[60vh] space-y-4 overflow-y-auto pr-1">
          <section data-swarm-agents="" className="space-y-1.5">
            <h3 className="text-xs font-medium text-muted-foreground">Agents ({agents.length}) — a headless swarm: you steer, you do not chat</h3>
            {agents.length === 0 && <p className="text-xs text-muted-foreground">No agents on record for this swarm.</p>}
            {agents.map(agent => (
              <div key={agent.id} className="flex items-center justify-between gap-3 rounded-lg border border-border/60 px-3 py-1.5 text-xs">
                <Mono className="truncate">{agent.id}</Mono>
                <span className="shrink-0 text-muted-foreground">{agent.status} · last seen <RelTime seconds={agent.heartbeat_age_s} /> ago</span>
              </div>
            ))}
          </section>
          <section data-swarm-intents="" className="space-y-1.5">
            <h3 className="text-xs font-medium text-muted-foreground">Open work ({intents.length}) — the lanes and what the autopilot queued</h3>
            {intents.length === 0 && <p className="text-xs text-muted-foreground">No open work — when everything finishes this swarm marks itself done.</p>}
            {intents.map(intent => (
              <div key={intent.id} className="rounded-lg border border-border/60 px-3 py-1.5 text-xs leading-5">
                <span className="text-muted-foreground">{intent.state === "open" ? "queued" : "running"}:</span> {intent.text}
              </div>
            ))}
          </section>
          {steerOpen && (
            <section className="space-y-1.5">
              <h3 className="text-xs font-medium text-muted-foreground">Say something to THIS swarm only</h3>
              <Textarea value={message} onChange={e => setMessage(e.target.value)} rows={2} className="text-sm" placeholder="focus on the /auth endpoints next" />
            </section>
          )}
          <SwarmActivity swarmId={swarmId} />
        </div>
        <DialogFooter className="flex-wrap">
          {swarm?.status === "paused"
            ? <Button type="button" size="sm" variant="secondary" disabled={pending} onClick={() => act("resume")}><Play className="size-3.5" />Resume</Button>
            : <Button type="button" size="sm" variant="secondary" disabled={pending} onClick={() => act("pause")}><Pause className="size-3.5" />Pause</Button>}
          {!steerOpen && <Button type="button" size="sm" variant="secondary" onClick={() => { setSteerOpen(true); setMessage(""); }}><Send className="size-3.5" />Say</Button>}
          {steerOpen && <Button type="button" size="sm" disabled={pending || !message.trim()} onClick={() => { void run("/api/ops/swarm/steer", { swarm: swarmId, message }, { ok: "said on the mesh" }); setSteerOpen(false); }}>Send to this swarm</Button>}
          <Button type="button" size="sm" variant="secondary" disabled={pending} onClick={() => act("stop")}><Square className="size-3.5" />Stop this swarm</Button>
          <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={() => setDeleteOpen(true)}><Trash2 className="size-3.5" />Delete</Button>
        </DialogFooter>
        <ConfirmDialog open={deleteOpen} onOpenChange={setDeleteOpen} title={`Delete ${swarm?.title ?? "this swarm"}?`}
          description="Stop first, then remove: the folder, the roster rows and the definition go together. Findings already recorded stay on the Findings page."
          confirmLabel="Stop and delete" tone="danger"
          onConfirm={() => { void run(`/api/ops/swarms/${swarmId}`, undefined, { method: "DELETE", ok: "swarm removed" }); onClose(); }} />
      </DialogContent>
    </Dialog>
  );
}

const KIND_LABEL: Record<string, string> = {
  "user.message": "asked",
  "assistant.reasoning": "thought",
  "assistant.message": "said",
  "tool.call": "called",
  "tool.result": "result",
  "phase.transition": "phase",
};

/** Live activity, newest last — the headless swarm's heartbeat (the Devin/OpenHands
 *  pattern: one feed where each row names what the agent just did). */
function SwarmActivity({ swarmId }: { swarmId: string | undefined }) {
  const query = useSwarmActivity(swarmId);
  const rows = (query.data?.rows ?? []).slice(-10);
  return (
    <section data-swarm-activity="" className="space-y-1.5">
      <h3 className="text-xs font-medium text-muted-foreground">Activity — the journal's last lines, live</h3>
      {rows.length === 0 && <p className="text-xs text-muted-foreground">Nothing on the journal yet.</p>}
      {rows.map((row, index) => {
        const text = String(row.text || row.content || row.output || row.title || row.id || "").slice(0, 160);
        return (
          <div key={row.id ?? index} className="rounded-lg border border-border/60 px-3 py-1.5 text-xs leading-5">
            <span className="mr-2 text-muted-foreground">{KIND_LABEL[row.kind] ?? row.kind}</span>
            <span className="break-words">{text}</span>
          </div>
        );
      })}
    </section>
  );
}

/** The swarm list — the first panel of the Swarm page. */
export function SwarmsPanel() {
  const query = useSwarms();
  const swarms = query.data?.swarms ?? [];
  const live = swarms.filter(swarm => swarm.status === "running");
  const [defineOpen, setDefineOpen] = useState(false);
  const [openId, setOpenId] = useState<string | undefined>(undefined);
  return (
    <Panel
      title={<span className="flex items-center gap-2"><Layers className="size-4" />Swarms ({swarms.length})</span>}
      description="One folder per swarm under the target — definition, journal, inbox, exploits and findings live together. Legacy engagement folders stay in the archive."
      actions={<Button type="button" size="sm" variant="secondary" onClick={() => setDefineOpen(true)}><FolderPlus className="size-3.5" />Define a swarm</Button>}
      flush
    >
      <FlashBar />
      <table className="w-full table-fixed text-left text-xs">
        <thead><tr>
          <Th className="w-24">Status</Th><Th>Mission</Th><Th className="w-40">Target</Th><Th className="w-20 text-right">Lanes</Th><Th className="w-24 text-right">Agents asked</Th><Th className="w-24 text-right">Created</Th><Th className="w-40"></Th>
        </tr></thead>
        <tbody>
          {query.isLoading && <EmptyRow colSpan={7}><span className="text-muted-foreground">Reading the registry…</span></EmptyRow>}
          {!query.isLoading && swarms.length === 0 && (
            <EmptyRow colSpan={7}>
              <span className="text-muted-foreground">No swarms yet — define one, or ask a chat to start an engagement and it lands here.</span>
            </EmptyRow>
          )}
          {swarms.map(swarm => (
            <tr key={swarm.id} className="border-t border-border/60 hover:bg-muted/30">
              <Td><StateBadge tone={swarmTone(swarm.status)} pulse={swarm.status === "running"}>{swarm.status}</StateBadge></Td>
              <Td><button type="button" className="max-w-full truncate text-left font-medium text-foreground hover:underline" onClick={() => setOpenId(swarm.id)} title={swarm.title}>{swarm.title || swarm.id}</button></Td>
              <Td><Mono className="truncate">{swarm.target}</Mono></Td>
              <Td className="text-right tabular-nums">{swarm.lanes?.length ?? 0}</Td>
              <Td className="text-right tabular-nums">{swarm.agents ? `${swarm.agents.host}+${swarm.agents.boxed}b` : "—"}</Td>
              <Td className="text-right"><RelTime epoch={swarm.created ? Math.floor(Date.parse(swarm.created) / 1000) : undefined} /></Td>
              <Td><div className="flex justify-end gap-1.5">
                <Button type="button" size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => setOpenId(swarm.id)}>Open</Button>
                {swarm.status !== "legacy" && swarm.id.startsWith("swarm-") && (
                  <Button type="button" size="sm" variant="ghost" className="h-7 px-2 text-xs text-destructive" onClick={() => setOpenId(swarm.id)}><Trash2 className="size-3" /></Button>
                )}
              </div></Td>
            </tr>
          ))}
        </tbody>
      </table>
      <DefineSwarmDialog open={defineOpen} onOpenChange={setDefineOpen} />
      {openId && <SwarmDetailDialog swarmId={openId} onClose={() => setOpenId(undefined)} />}
    </Panel>
  );
}
