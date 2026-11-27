import type { ConsoleQueuePauseReason, ConsoleSessionSnapshot } from "@0/engine/shared";

export type QueueState =
  | { kind: "waiting"; label: string; sendNowHint: string }
  | { kind: "paused"; reason: ConsoleQueuePauseReason; label: string; sendNowHint: string };

const PAUSED_LABEL: Record<ConsoleQueuePauseReason, string> = {
  stopped: "Paused because you stopped the turn. Send now to continue",
  error: "Paused because the last turn failed. Send now to retry",
  restored: "Paused while this thread was closed. Send now to continue",
};

/**
 * Queued messages send on their own when the running turn ends. They are only
 * held back when the gateway says so (the turn was stopped, it failed, or the
 * thread was reopened), and the card then names the reason. An idle thread with
 * no reason is the moment between a turn ending and the next one starting.
 */
export function queueState(snapshot: Pick<ConsoleSessionSnapshot, "queuePausedReason" | "session">): QueueState {
  const reason = snapshot.queuePausedReason;
  if (reason) return { kind: "paused", reason, label: PAUSED_LABEL[reason], sendNowHint: "Send this message now" };
  const idle = snapshot.session.status === "ready";
  return {
    kind: "waiting",
    label: idle ? "Queued · sending next" : "Queued · sends after the current response",
    sendNowHint: idle ? "Send this message now" : "Interrupt the current response and send this message now",
  };
}
