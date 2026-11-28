/**
 * The roster and the work queue: every agent the swarm knows, and every intent — with the
 * controls the terminal hub has: pause, resume, stop, steer, queue work, take it back.
 */
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Crosshair, ListPlus, MessageSquare, Pause, Play, Rocket, Square, Target, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip } from "@/components/ui/tooltip";
import { Table, TableBody, TableHeader, TableRow } from "@/components/ui/table";
import { OPS_KEY, useOps, useRun, type OpsAgent, type OpsIntent, type OpsOverview } from "./api";
import { ConfirmDialog, EmptyRow, ErrorNote, Mono, Panel, RelTime, StateBadge, Td, Th, Tone, useFlash, fmtDur } from "./ui";

function agentState(agent: OpsAgent): { label: string; tone: Tone } {
  if (agent.status === "paused") return { label: "Paused", tone: "amber" };
  if (agent.status === "stopped") return { label: "Stopping", tone: "neutral" };
  if (agent.alive) return { label: "Alive", tone: "emerald" };
  return { label: "Gone", tone: "neutral" };
}

export function AgentsPanel({ overview }: { overview: OpsOverview }) {
  const agents = overview.agents;
  const sessionIds = useMemo(() => new Set(overview.sessions.map((session) => session.id)), [overview.sessions]);
  const [steer, setSteer] = useState<OpsAgent | null>(null);
  const [message, setMessage] = useState("");
  const [log, setLog] = useState<OpsAgent | null>(null);
  const [stop, setStop] = useState<OpsAgent | null>(null);
  const [mission, setMission] = useState<OpsAgent | null>(null);
  const [missionText, setMissionText] = useState("");
  const { run, pending } = useRun();
  const ops = useOps();
  const logQuery = useQuery({
    queryKey: ["ops", "agent-log", log?.id],
    queryFn: ({ signal }) => ops.get<{ lines: string[]; note?: string }>(`/api/ops/agents/${encodeURIComponent(log?.id ?? "")}/log`, undefined, signal),
    enabled: Boolean(log),
    refetchInterval: log ? 3000 : false,
  });

  const act = (agent: OpsAgent, action: "pause" | "resume" | "stop") =>
    void run(`/api/ops/agents/${encodeURIComponent(agent.id)}/${action}`, {}, { ok: `${action === "pause" ? "Paused" : action === "resume" ? "Resumed" : "Stopping"} ${agent.id}` });

  return (
    <Panel
      title="Agents"
      description={agents.length ? `${overview.counts.alive} alive of ${agents.length} on the roster. Finished agents leave it after about five minutes.` : "Every agent the swarm knows: host agents and boxed ones, leader, supervisors and workers."}
      flush
    >
      <Table>
        <TableHeader>
          <TableRow>
            <Th>Agent</Th><Th>Swarm</Th><Th>Working on</Th><Th>State</Th><Th>Heartbeat</Th><Th>Uptime</Th><Th className="text-right">Actions</Th>
          </TableRow>
        </TableHeader>
        <TableBody>
          {!agents.length && (
            <EmptyRow colSpan={7}>
              No agents yet. <Link to="/chats?new=1&kind=engagement" className="text-primary-text underline-offset-4 hover:underline">Start an engagement</Link> or queue work below.
            </EmptyRow>
          )}
          {agents.map((agent) => {
            const state = agentState(agent);
            return (
              <TableRow key={agent.id} data-agent-id={agent.id}>
                <Td className="whitespace-nowrap">
                  <div className="flex items-center gap-1.5">
                    <Mono>{agent.id}</Mono>
                    {agent.boxed && <Badge variant="info">boxed</Badge>}
                    {agent.tier && <Badge variant="accent">{agent.tier.split("-")[1]}</Badge>}
                  </div>
                </Td>
                <Td>
                  {sessionIds.has(String(agent.swarm ?? "")) && agent.swarm !== "default"
                    ? <Link to={`/chats/${agent.swarm}`} className="font-mono text-[12px] text-primary-text underline-offset-4 hover:underline">{agent.swarm}</Link>
                    : <Mono className="text-muted-foreground">{agent.swarm || "—"}</Mono>}
                </Td>
                <Td className="max-w-[28rem]"><div className="truncate" title={agent.lane ?? ""}>{agent.lane === "idle" ? <span className="text-muted-foreground">idle</span> : agent.lane}</div></Td>
                <Td><StateBadge tone={state.tone}>{state.label}</StateBadge></Td>
                <Td><RelTime epoch={agent.last_heartbeat} /></Td>
                <Td><RelTime epoch={agent.joined_at} /></Td>
                <Td>
                  <div className="flex items-center justify-end gap-0.5">
                    {agent.status === "paused"
                      ? <Tooltip content="Resume"><Button type="button" variant="ghost" size="icon-xs" aria-label={`Resume ${agent.id}`} onClick={() => act(agent, "resume")} disabled={pending}><Play className="size-3.5" /></Button></Tooltip>
                      : <Tooltip content="Pause after the current step"><Button type="button" variant="ghost" size="icon-xs" aria-label={`Pause ${agent.id}`} onClick={() => act(agent, "pause")} disabled={pending || !agent.alive}><Pause className="size-3.5" /></Button></Tooltip>}
                    <Tooltip content="Replace its standing mission"><Button type="button" variant="ghost" size="icon-xs" aria-label={`Set the mission of ${agent.id}`} onClick={() => { setMission(agent); setMissionText(""); }} disabled={!agent.alive}><Target className="size-3.5" /></Button></Tooltip>
                    <Tooltip content="Steer with a message"><Button type="button" variant="ghost" size="icon-xs" aria-label={`Steer ${agent.id}`} onClick={() => { setSteer(agent); setMessage(""); }} disabled={!agent.alive}><MessageSquare className="size-3.5" /></Button></Tooltip>
                    <Tooltip content="Stop this agent"><Button type="button" variant="ghost" size="icon-xs" aria-label={`Stop ${agent.id}`} onClick={() => setStop(agent)} disabled={!agent.alive}><Square className="size-3.5" /></Button></Tooltip>
                  </div>
                </Td>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      <ConfirmDialog open={Boolean(steer)} onOpenChange={(open) => !open && setSteer(null)} title={`Steer ${steer?.id ?? ""}`}
        description="The agent reads your message at its next step and treats it as an operator instruction." confirmLabel="Send" pending={pending}
        onConfirm={() => { if (steer) void run(`/api/ops/agents/${encodeURIComponent(steer.id)}/steer`, { message }, { ok: `Steered ${steer.id}` }).then(() => setSteer(null)); }}>
        <Textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Look at the login flow again before moving on." rows={4} className="mt-3" maxLength={500} />
      </ConfirmDialog>

      <ConfirmDialog open={Boolean(mission)} onOpenChange={(open) => !open && setMission(null)} title={`Mission for ${mission?.id ?? ""}`}
        description="Replaces what the agent is working toward. A steer adds to its current work; a mission replaces it." confirmLabel="Set mission" pending={pending}
        onConfirm={() => { if (mission) void run(`/api/ops/agents/${encodeURIComponent(mission.id)}/mission`, { mission: missionText }, { ok: `Mission set for ${mission.id}` }).then((result) => result && setMission(null)); }}>
        <Textarea value={missionText} onChange={(event) => setMissionText(event.target.value)} rows={4} maxLength={500} className="mt-3" placeholder="Map every endpoint under the API and record what each one exposes." />
      </ConfirmDialog>

      <ConfirmDialog open={Boolean(stop)} onOpenChange={(open) => !open && setStop(null)} title={`Stop ${stop?.id ?? ""}`} tone="warning"
        description="The agent finishes the tool call it is on, then exits. Its engagement keeps its history; send a message there to start it again."
        confirmLabel="Stop agent" pending={pending}
        onConfirm={() => { if (stop) void run(`/api/ops/agents/${encodeURIComponent(stop.id)}/stop`, {}, { ok: `Stopping ${stop.id}` }).then(() => setStop(null)); }} />

      <ConfirmDialog open={Boolean(log)} onOpenChange={(open) => !open && setLog(null)} title={`${log?.id ?? ""} — log`} confirmLabel="Close" onConfirm={() => setLog(null)}>
        <pre data-agent-log="" className="mt-3 max-h-[480px] overflow-auto rounded-[var(--radius-control,8px)] bg-muted p-3 font-mono text-[12px] leading-5 text-foreground">
          {logQuery.data?.lines?.length ? logQuery.data.lines.join("\n") : (logQuery.data?.note ?? "No log yet.")}
        </pre>
      </ConfirmDialog>
    </Panel>
  );
}

const INTENT_TONE: Record<string, Tone> = { open: "sky", running: "emerald", blocked: "amber", exhausted: "amber", done: "neutral", stopped: "neutral" };

export function QueuePanel({ overview }: { overview: OpsOverview }) {
  const intents = overview.intents;
  const [text, setText] = useState("");
  const [swarm, setSwarm] = useState("default");
  const [remove, setRemove] = useState<OpsIntent | null>(null);
  const [refused, setRefused] = useState<{ message: string; detail?: string } | null>(null);
  const [stopSwarm, setStopSwarm] = useState(false);
  const [mission, setMission] = useState(false);
  const [missionText, setMissionText] = useState("");
  const { run, pending } = useRun();
  const { show } = useFlash();
  const ops = useOps();
  const queryClient = useQueryClient();
  const swarms = useMemo(() => {
    const ids = new Set<string>(["default"]);
    for (const session of overview.sessions) if (session.id) ids.add(session.id);
    for (const agent of overview.agents) if (agent.swarm) ids.add(String(agent.swarm));
    return [...ids];
  }, [overview.sessions, overview.agents]);
  const counts = overview.counts;

  const queue = async (force = false) => {
    if (!text.trim()) return;
    try {
      await ops.send<{ id: string; open: number }>("/api/ops/intents", "POST", { text: text.trim(), swarm, force });
      setRefused(null);
      setText("");
      show(`Queued in ${swarm}. An idle agent picks it up.`, "status");
      void queryClient.invalidateQueries({ queryKey: OPS_KEY });
    } catch (error) {
      // the prior-work gate refuses with its reason; the operator can queue anyway
      setRefused({ message: error instanceof Error ? error.message : String(error) });
    }
  };
  const swarmVerb = (action: "pause" | "resume" | "stop") =>
    void run(`/api/ops/swarm/${action}`, { swarm }, { ok: `${action === "pause" ? "Paused" : action === "resume" ? "Resumed" : "Stopping"} ${swarm}` });

  return (
    <Panel
      title="Queue"
      description="Work waiting for an agent. An open intent anywhere holds the autopilot; a running one is being worked. The controls act on the selected swarm."
      flush
      actions={
        <div className="flex flex-wrap items-center gap-1.5">
          {(["open", "running", "blocked", "done"] as const).map((state) => (
            <Badge key={state} variant="neutral" className="gap-1.5 font-normal">
              <span className="w-4 text-right tabular-nums">{counts[state] ?? 0}</span>
              {state}
            </Badge>
          ))}
        </div>
      }
    >
      <form className="flex flex-wrap items-center gap-2 border-b border-border/60 p-3" onSubmit={(event) => { event.preventDefault(); void queue(false); }}>
        <Input
          value={text}
          onChange={(event) => { setText(event.target.value); setRefused(null); }}
          placeholder="Describe the work — e.g. test the login form of a target for injection"
          aria-label="New intent"
          className="min-w-[16rem] flex-1"
          maxLength={4000}
        />
        <Select
          value={swarm}
          onValueChange={(value) => { setSwarm(value); setRefused(null); }}
          options={swarms.map((id) => ({ value: id, label: id === "default" ? "default swarm" : id }))}
          aria-label="Queue for"
          className="w-auto min-w-[10rem]"
        />
        {/* the four things one can do to a swarm, kept together so they read as one control */}
        <div data-swarm-verbs="" className="inline-flex items-center rounded-[var(--radius-control,8px)] border border-border/60">
          <Tooltip content="Pause every agent of this swarm">
            <Button type="button" variant="ghost" size="icon-sm" aria-label={`Pause ${swarm}`} disabled={pending} onClick={() => swarmVerb("pause")}><Pause className="size-3.5" /></Button>
          </Tooltip>
          <Tooltip content="Resume every agent of this swarm">
            <Button type="button" variant="ghost" size="icon-sm" aria-label={`Resume ${swarm}`} disabled={pending} onClick={() => swarmVerb("resume")}><Play className="size-3.5" /></Button>
          </Tooltip>
          <Tooltip content="Stop every agent of this swarm">
            <Button type="button" variant="ghost" size="icon-sm" aria-label={`Stop ${swarm}`} disabled={pending} onClick={() => setStopSwarm(true)}><Square className="size-3.5" /></Button>
          </Tooltip>
          <Tooltip content="Replace the standing mission of this swarm">
            <Button type="button" variant="ghost" size="icon-sm" aria-label={`Set the mission of ${swarm}`} disabled={pending} onClick={() => { setMission(true); setMissionText(""); }}><Target className="size-3.5" /></Button>
          </Tooltip>
        </div>
        <Button type="submit" variant="accent" size="sm" disabled={pending || !text.trim()}><ListPlus className="size-3.5" />Queue</Button>
      </form>
      {refused && (
        <div className="flex flex-wrap items-start gap-3 border-b border-border/60 bg-amber-500/10 px-4 py-2.5" data-queue-refused="">
          <ErrorNote error={refused.message} />
          <Button type="button" size="sm" variant="warning" disabled={pending} onClick={() => void queue(true)}>Queue anyway</Button>
        </div>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <Th>State</Th><Th>Work</Th><Th>Swarm</Th><Th>Agent</Th><Th>Queued</Th><Th>Held</Th><Th>Tries</Th><Th className="w-10" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {!intents.length && <EmptyRow colSpan={8}>The queue is empty.</EmptyRow>}
          {intents.map((intent) => {
            const state = String(intent.state ?? "open");
            const removable = state === "open" && !intent.agent_id;
            return (
              <TableRow key={intent.id} data-intent-id={intent.id} data-intent-state={state}>
                <Td><StateBadge tone={INTENT_TONE[state] ?? "neutral"}>{state}</StateBadge></Td>
                <Td className="max-w-[36rem]"><div className="truncate" title={intent.text}>{intent.text}</div></Td>
                <Td><Mono className="text-muted-foreground">{intent.swarm || "default"}</Mono></Td>
                <Td><Mono className="text-muted-foreground">{intent.agent_id ?? "—"}</Mono></Td>
                <Td><RelTime epoch={intent.created_at} /></Td>
                <Td>{intent.held_s != null ? <span className="tabular-nums text-muted-foreground">{fmtDur(intent.held_s)}</span> : <span className="text-muted-foreground">—</span>}</Td>
                <Td className="tabular-nums text-muted-foreground">{intent.attempts ?? 0}</Td>
                <Td>
                  {removable && (
                    <Tooltip content="Take it back">
                      <Button type="button" variant="ghost" size="icon-xs" aria-label="Remove from the queue" onClick={() => setRemove(intent)}><Trash2 className="size-3.5" /></Button>
                    </Tooltip>
                  )}
                </Td>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <ConfirmDialog
        open={Boolean(remove)}
        onOpenChange={(open) => !open && setRemove(null)}
        title="Take this work back?"
        tone="warning"
        description={<span>No agent has picked it up: <Mono>{remove?.text?.slice(0, 120)}</Mono></span>}
        confirmLabel="Remove"
        pending={pending}
        onConfirm={() => { if (remove) void run(`/api/ops/intents/${encodeURIComponent(remove.id)}`, undefined, { method: "DELETE", ok: "Removed from the queue" }).then(() => setRemove(null)); }}
      />
      <ConfirmDialog
        open={stopSwarm}
        onOpenChange={setStopSwarm}
        title={`Stop every agent of ${swarm}?`}
        tone="warning"
        description="They finish their current step and exit. Their engagements keep their history; queue work or start agents here to run again."
        confirmLabel="Stop swarm"
        pending={pending}
        onConfirm={() => { swarmVerb("stop"); setStopSwarm(false); }}
      />
      <ConfirmDialog
        open={mission}
        onOpenChange={setMission}
        title={`Mission for ${swarm}`}
        confirmLabel="Set mission"
        pending={pending}
        description="Replaces what every agent of this swarm is working toward."
        onConfirm={() => { void run("/api/ops/swarm/mission", { mission: missionText, swarm }, { ok: `Mission set for ${swarm}` }).then((result) => result && setMission(false)); }}
      >
        <Textarea value={missionText} onChange={(event) => setMissionText(event.target.value)} rows={4} maxLength={500} className="mt-3" placeholder="Map every endpoint under the API and record what each one exposes." />
      </ConfirmDialog>
    </Panel>
  );
}

/** Start agents in a swarm by hand: host agents as their own processes, boxed agents in sandboxes. */
export function SpawnPanel({ overview }: { overview: OpsOverview }) {
  const { run, pending } = useRun();
  const [swarm, setSwarm] = useState("default");
  const [host, setHost] = useState("1");
  const [boxed, setBoxed] = useState("0");
  const [mission, setMission] = useState("");
  const ready = overview.coordinator.running;
  const engine = overview.boxes.engine === "running";
  const image = overview.boxes.image?.exists ?? false;
  const swarms = useMemo(() => {
    const ids = new Set<string>(["default"]);
    for (const session of overview.sessions) if (session.id) ids.add(session.id);
    for (const agent of overview.agents) if (agent.swarm) ids.add(String(agent.swarm));
    return [...ids];
  }, [overview.sessions, overview.agents]);
  const nHost = Math.max(0, Math.min(8, Number(host) || 0));
  const nBoxed = Math.max(0, Math.min(16, Number(boxed) || 0));
  const boxedBlocked = nBoxed > 0 && (!engine || !image);
  const total = nHost + nBoxed;
  const start = () =>
    void run<{ spawned: { host: { id: string }[]; boxed: { id: string; box: string }[] } }>(
      "/api/ops/swarm/spawn",
      { swarm, host: nHost, boxed: nBoxed, mission: mission.trim() || undefined },
      { ok: `${nHost} host and ${nBoxed} boxed agent(s) started in ${swarm}` },
    );
  return (
    <Panel title="Start agents" description="Bring agents into a swarm by hand — the terminal's swarm spawn. Host agents run as their own processes; boxed agents run in sandboxes.">
      <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); start(); }} data-spawn-form="">
        <label className="space-y-1 text-xs text-muted-foreground">
          Swarm
          <Select value={swarm} onValueChange={setSwarm} options={swarms.map((id) => ({ value: id, label: id === "default" ? "default swarm" : id }))} aria-label="Start agents in" className="w-auto min-w-[10rem]" />
        </label>
        <label className="space-y-1 text-xs text-muted-foreground">
          Host agents
          <Input type="number" min={0} max={8} value={host} onChange={(event) => setHost(event.target.value)} aria-label="Host agents" className="w-24 tabular-nums" />
        </label>
        <label className="space-y-1 text-xs text-muted-foreground">
          Boxed agents
          <Input type="number" min={0} max={16} value={boxed} onChange={(event) => setBoxed(event.target.value)} aria-label="Boxed agents" className="w-24 tabular-nums" />
        </label>
        <label className="min-w-[16rem] flex-1 space-y-1 text-xs text-muted-foreground">
          Mission <span className="opacity-70">(optional — worked when the queue runs dry)</span>
          <Input value={mission} onChange={(event) => setMission(event.target.value)} placeholder="Map every endpoint under the API and record what each one exposes." aria-label="Mission" maxLength={500} />
        </label>
        <Button type="submit" variant="accent" size="sm" disabled={pending || !ready || boxedBlocked || total === 0} data-spawn-submit="">
          <Rocket className="size-3.5" />Start {total || ""} agent{total === 1 ? "" : "s"}
        </Button>
      </form>
      <div className="mt-3 space-y-1.5 text-xs leading-5 text-muted-foreground">
        {!ready && (
          <div className="flex flex-wrap items-center gap-2">
            <Crosshair className="size-3.5" aria-hidden="true" /> The coordinator is not running — agents register with it. Start it from the Coordinator panel first.
          </div>
        )}
        {nBoxed > 0 && !engine && <div>Boxed agents need a container engine that is already running (OrbStack or Docker). Suijin never starts one for you.</div>}
        {nBoxed > 0 && engine && !image && <div>The sandbox image is not built — build it under Boxes first.</div>}
        {ready && !boxedBlocked && <div>Each host agent is a full Suijin process (about 220 MB); each boxed agent about 160 MB. A plan that does not fit in free memory is refused with the largest safe count.</div>}
      </div>
    </Panel>
  );
}
