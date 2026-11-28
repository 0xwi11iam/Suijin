import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { OctagonX, Pause, Play, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { PageContent, PageHeader } from "@/components/page-header";
import { ErrorState, LoadingState } from "@/components/state-panel";
import { useOverview, useRun } from "@/ops/api";
import { AgentsPanel, QueuePanel, SpawnPanel } from "@/ops/swarm-agents";
import { BoxesPanel } from "@/ops/swarm-boxes";
import { CoordinatorPanel, FleetPanel, JobsPanel, MeshPanel, StructuredPanel, SystemPanel } from "@/ops/swarm-fleet";
import { SwarmsPanel } from "@/ops/swarms";
import { ConfirmDialog, ErrorNote, FlashBar, Stat } from "@/ops/ui";

export function SwarmPage() {
  const [search] = useSearchParams();
  const deepSwarm = search.get("swarm") || undefined;
  const query = useOverview();
  const { run, pending } = useRun();
  const [steerOpen, setSteerOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [stopOpen, setStopOpen] = useState(false);
  const overview = query.data;

  if (query.error && !overview) return <PageContent><ErrorState error={query.error} /></PageContent>;
  if (!overview) return <PageContent><LoadingState label="Reading the swarm" variant="detail" /></PageContent>;

  const counts = overview.counts;
  const sandboxes = overview.boxes.sandboxes ?? [];
  const running = sandboxes.filter((box) => box.running).length;
  const fleetPaused = overview.fleet.pause_flag;
  const errorEntries = Object.entries(overview.errors);

  return (
    <PageContent>
      <PageHeader
        title="Swarm"
        summary="Suijin's runtime, in one place: the coordinator, every agent, the work queue, the boxes and the warm pile."
        actions={(
          <>
            <Button type="button" size="sm" variant="secondary" disabled={pending} onClick={() => void run(`/api/ops/swarm/${fleetPaused ? "resume" : "pause"}`, {}, { ok: fleetPaused ? "Fleet resumed" : "Fleet pausing" })}>
              {fleetPaused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}{fleetPaused ? "Resume fleet" : "Pause fleet"}
            </Button>
            <Button type="button" size="sm" variant="secondary" onClick={() => { setSteerOpen(true); setMessage(""); }}><Send className="size-3.5" />Steer the fleet</Button>
            <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={() => setStopOpen(true)}><OctagonX className="size-3.5" />Stop everything</Button>
          </>
        )}
      />

      <FlashBar />

      <SwarmsPanel initialSwarm={deepSwarm} />

      {errorEntries.length > 0 && (
        <ErrorNote error="Some sources did not answer">
          <ul className="list-disc space-y-0.5 pl-4">{errorEntries.map(([name, reason]) => <li key={name}><span className="font-medium">{name}</span>: {reason}</li>)}</ul>
        </ErrorNote>
      )}

      <div className="grid grid-cols-2 gap-2 lg:grid-cols-3 xl:grid-cols-6">
        <Stat label="Coordinator" tone={overview.coordinator.running ? "emerald" : "neutral"} value={overview.coordinator.running ? "running" : "stopped"} hint={overview.coordinator.bus || "no bus"} />
        <Stat label="Agents" tone={counts.alive ? "emerald" : "neutral"} value={counts.alive} hint={`of ${counts.agents} on the roster${counts.boxed ? ` · ${counts.boxed} boxed` : ""}${counts.paused ? ` · ${counts.paused} paused` : ""}`} />
        <Stat label="Queue" tone={counts.running ? "sky" : counts.open ? "amber" : "neutral"} value={counts.open + counts.running} hint={`${counts.open} open · ${counts.running} running${counts.blocked ? ` · ${counts.blocked} blocked` : ""}`} />
        <Stat label="Findings" tone={overview.findings.confirmed ? "emerald" : "neutral"} value={overview.findings.total} hint={`${overview.findings.confirmed} confirmed`} />
        <Stat label="Sandboxes" tone={running ? "sky" : "neutral"} value={running} hint={`${sandboxes.length - running} stopped`} />
        <Stat label="Warm pile" tone={(overview.boxes.pool?.size ?? 0) ? "sky" : "neutral"} value={overview.boxes.pool?.size ?? 0} hint={`target ${overview.boxes.pool?.target ?? 0}`} />
      </div>

      <AgentsPanel overview={overview} />
      <QueuePanel overview={overview} />
      <SpawnPanel overview={overview} />
      <BoxesPanel overview={overview} />
      <div className="grid gap-6 xl:grid-cols-2">
        <CoordinatorPanel overview={overview} />
        <FleetPanel overview={overview} />
      </div>
      <MeshPanel overview={overview} />
      <StructuredPanel overview={overview} />
      <div className="grid gap-6 xl:grid-cols-2">
        <SystemPanel overview={overview} />
        <JobsPanel overview={overview} />
      </div>

      <ConfirmDialog open={steerOpen} onOpenChange={setSteerOpen} title="Steer the whole fleet" confirmLabel="Send" pending={pending}
        description="Every agent reads this at its next step and treats it as an operator instruction."
        onConfirm={() => { void run("/api/ops/swarm/steer", { message }, { ok: "Sent to every agent" }).then((result) => result && setSteerOpen(false)); }}>
        <Textarea value={message} onChange={(event) => setMessage(event.target.value)} rows={4} maxLength={500} className="mt-3" placeholder="Wrap up what you are on and write up what you have." />
      </ConfirmDialog>

      <ConfirmDialog open={stopOpen} onOpenChange={setStopOpen} title="Stop everything?" tone="danger" typed="stop" confirmLabel="Stop everything" pending={pending}
        description="This is swarm stop: every agent finishes its current step and exits, the coordinator goes down, and every sandbox and the warm pile is removed. Chats, findings and engagement history stay exactly as they are."
        onConfirm={() => { void run("/api/ops/swarm/stop-all", {}, { ok: "Everything stopped" }).then((result) => result && setStopOpen(false)); }} />
    </PageContent>
  );
}
