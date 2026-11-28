/**
 * The typewriter behind a live reply: reasoning and text that arrive in one piece are revealed a
 * few hundred characters a second instead of appearing whole.
 *
 * The arithmetic lives here, free of React, so it is decided by tests. `useTypewriter` (./use-typewriter)
 * drives it once per animation frame.
 */

/** Thinking reads slower than the answer. */
export const REASONING_CHARS_PER_SECOND = 320;
export const REPLY_CHARS_PER_SECOND = 700;
/** Shorter than this is shown at once — typing a dozen characters is a flicker, not an effect. */
export const MIN_CHARS_TO_TYPE = 24;

/**
 * The reveal count after `dtSeconds` at `charsPerSecond`. Moves toward `target` only: a replaced
 * text that is shorter than the reveal pulls it back, and a zero step leaves it alone.
 */
export function nextShown(shown: number, target: number, dtSeconds: number, charsPerSecond: number): number {
  const capped = Math.min(shown, target);
  if (dtSeconds <= 0) return capped;
  return Math.min(target, capped + Math.max(1, Math.floor(dtSeconds * charsPerSecond)));
}

/**
 * Whether a piece of text types out. Only text that belongs to a turn still (or just) working:
 * never under reduced motion, never text restored from history (a reload shows a finished chat at
 * once), and never text that appears together with the transcript itself (opening a chat mid-turn
 * must not retype what already happened).
 *
 * `fresh` — the turn is working now, or was a moment ago: a final reply lands in the same poll as
 * "done", so "live" alone would never type the answer.
 */
export function shouldAnimate({ fresh, reducedMotion, textLength, history }: { fresh: boolean; reducedMotion: boolean; textLength: number; history: boolean }): boolean {
  return fresh && !reducedMotion && !history && textLength >= MIN_CHARS_TO_TYPE;
}

/** How long after a transcript mounts its own pieces count as history rather than fresh arrivals. */
const HISTORY_WINDOW_MS = 250;
let transcriptMountedAt = Number.NEGATIVE_INFINITY;

/** Called when a transcript mounts (and when it switches to another chat), so the pieces that come
 * with it are shown rather than typed. */
export function markTranscriptMount(): void {
  transcriptMountedAt = performance.now();
}

/** True inside the window after a transcript mounted: what appears now came with the transcript. */
export function mountedWithTranscript(): boolean {
  return performance.now() - transcriptMountedAt < HISTORY_WINDOW_MS;
}

/** How long a turn counts as fresh after it was last seen working. */
const FRESH_WINDOW_MS = 1500;
const workingAt = new Map<string, number>();

/** Called on every render of a working turn; the map only ever holds turns seen this page session. */
export function markTurnWorking(turnId: string): void {
  workingAt.set(turnId, performance.now());
}

/** Was this turn seen working a moment ago? Turns never seen working — history — are not fresh. */
export function wasWorkingRecently(turnId: string): boolean {
  const at = workingAt.get(turnId);
  return at !== undefined && performance.now() - at < FRESH_WINDOW_MS;
}

/** The visible prefix. Never splits a surrogate pair, so an emoji or a CJK extension character is
 * shown whole or not at all. */
export function shownSlice(text: string, shown: number): string {
  if (shown >= text.length) return text;
  const index = Math.max(0, Math.floor(shown));
  // A high surrogate at the cut belongs to the character after it: leave both for the next step.
  const code = text.charCodeAt(index - 1);
  return text.slice(0, code >= 0xd800 && code <= 0xdbff ? index - 1 : index);
}
