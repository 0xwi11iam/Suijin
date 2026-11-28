import { memo, Fragment, useEffect, useState } from "react";
import { REASONING_CHARS_PER_SECOND, REPLY_CHARS_PER_SECOND } from "./typewriter";
import { useTypewriter } from "./use-typewriter";
import { Markdown } from "./markdown";
import { ToolActivity } from "./tool-activity";
import { InlineImage, transcriptToolImages } from "./inline-image";
import { ActivityMark } from "./loading-state";
import type { ReducedTurn } from "./transcript";

function TurnWorkStatus({ turn }: { turn: ReducedTurn }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { if (!turn.isWorking) return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [turn.isWorking]);
  // The status row lives only while the turn is live (operator 2026-11-28: finished turns
  // showed a bare "Worked" line with a rule under it — noise). Done is silence.
  if (!turn.isWorking) return null;
  const start = Date.parse(turn.startedAt ?? "");
  const end = turn.isWorking ? now : Date.parse(turn.endedAt ?? "");
  const elapsed = Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, Math.floor((end - start) / 1000)) : undefined;
  const duration = elapsed === undefined ? "" : elapsed >= 60 ? ` for ${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : ` for ${elapsed}s`;
  const label = turn.isWorking ? turn.toolCalls.length || turn.assistantText ? "Working" : "Thinking" : turn.stopReason === "cancelled" || turn.error ? "Stopped" : "Worked";
  return <div data-turn-work-status className="flex items-center gap-2 border-b border-border/60 pb-2 text-xs text-muted-foreground">{turn.isWorking && <ActivityMark compact />}<span className={turn.isWorking ? "console-working-label" : undefined}>{label}{duration}</span></div>;
}

/** Cheap content signature — polls rebuild turn OBJECTS every ~2s; without
 * this comparator every turn re-rendered (DOM churn) and mouse clicks died
 * while agents streamed (field 2026-11-17: "can't click open when agents
 * are thinking"). Equal signature = skip re-render, DOM stays clickable. */
function turnSignature(turn: ReducedTurn, streaming: boolean): string {
  return JSON.stringify([
    turn.id, turn.isWorking, turn.startedAt, turn.endedAt, turn.stopReason, turn.error, streaming,
    turn.assistantText?.length ?? 0, turn.reasoningText?.length ?? 0,
    (turn.segments ?? []).map(seg => [seg.type, seg.type === "assistant" || seg.type === "reasoning" ? seg.text.length : (seg.toolIds ?? []).length]),
    turn.toolCalls.map(call => [call.id, call.name, call.isRunning === true, JSON.stringify(call.result ?? "").length, JSON.stringify(call.arguments ?? {}).length]),
  ]);
}

/** A reply that arrived whole still types out when it is fresh (see useTypewriter). */
function AssistantText({ turnId, text, live }: { turnId: string; text: string; live: boolean }) {
  const shown = useTypewriter(text, { turnId, live, charsPerSecond: REPLY_CHARS_PER_SECOND });
  return <Markdown text={shown} streaming={live || shown.length < text.length} />;
}

/** The model's thinking, typed as it arrives. */
function ReasoningText({ turnId, text, live }: { turnId: string; text: string; live: boolean }) {
  const shown = useTypewriter(text, { turnId, live, charsPerSecond: REASONING_CHARS_PER_SECOND });
  return <ToolActivity calls={[]} reasoning={shown} working={live} />;
}

/** Render each tool batch beside the commentary that introduced it. */
export const TurnTimeline = memo(function TurnTimeline({ turn, streaming }: { turn: ReducedTurn; streaming: boolean }) {
  const segments = turn.segments?.length ? turn.segments : [
    ...(turn.reasoningText ? [{ type: "reasoning" as const, text: turn.reasoningText }] : []),
    ...(turn.toolCalls.length ? [{ type: "tools" as const, toolIds: turn.toolCalls.map(call => call.id) }] : []),
    ...(turn.assistantText ? [{ type: "assistant" as const, text: turn.assistantText }] : []),
  ];
  const latestToolIndex = segments.findLastIndex(segment => segment.type === "tools");
  const callsById = new Map(turn.toolCalls.map(call => [call.id, call]));
  return <div className="min-w-0 space-y-4">
    <TurnWorkStatus turn={turn} />
    {segments.map((segment, index) => {
      if (segment.type === "assistant") return <AssistantText key={index} turnId={turn.id} text={segment.text} live={streaming && index === segments.length - 1} />;
      if (segment.type === "reasoning") return <ReasoningText key={index} turnId={turn.id} text={segment.text} live={streaming && index === segments.length - 1} />;
      const calls = segment.toolIds.flatMap(id => { const call = callsById.get(id); return call ? [call] : []; });
      return <Fragment key={index}>
        <ToolActivity calls={calls} showLatest={index === latestToolIndex} working={turn.isWorking && index === latestToolIndex} />
        {calls.flatMap(call => transcriptToolImages(call.name, call.result)).map((img, imageIndex) => <InlineImage key={imageIndex} {...img} />)}
      </Fragment>;
    })}
  </div>;
}, (prev, next) => turnSignature(prev.turn, prev.streaming) === turnSignature(next.turn, next.streaming));
