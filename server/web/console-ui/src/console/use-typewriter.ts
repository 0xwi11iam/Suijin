import { useEffect, useRef, useState } from "react";
import { mountedWithTranscript, nextShown, shouldAnimate, shownSlice, wasWorkingRecently } from "./typewriter";

function useReducedMotion(): boolean {
  const query = () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null);
  const [reduced, setReduced] = useState(() => query()?.matches ?? false);
  useEffect(() => {
    const media = query();
    if (!media) return;
    const onChange = () => setReduced(media.matches);
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
}

/**
 * The part of `text` to show right now.
 *
 * Text that belongs to a working turn types out from wherever the reveal stands, and the reveal runs
 * to its end even if the turn finishes first (a final reply lands in the same poll as "done"). Text
 * restored from history, or that appears together with the transcript itself, is shown at once.
 */
export function useTypewriter(text: string, { turnId, live, charsPerSecond }: { turnId: string; live: boolean; charsPerSecond: number }): string {
  const reducedMotion = useReducedMotion();
  const typing = useRef(false);
  const shown = useRef<number | null>(null);
  const [, repaint] = useState(0);
  const fresh = () => live || wasWorkingRecently(turnId);

  if (shown.current === null) {
    // the first render of this piece decides: history is shown whole, a fresh arrival starts typing
    const animate = shouldAnimate({ fresh: fresh(), reducedMotion, textLength: text.length, history: mountedWithTranscript() });
    typing.current = animate;
    shown.current = animate ? 0 : text.length;
  }

  useEffect(() => {
    if (!typing.current) {
      // more text arrived for a turn still working: type the new part from where the reveal stands
      if (!shouldAnimate({ fresh: fresh(), reducedMotion, textLength: text.length, history: false })) {
        if (shown.current !== text.length) {
          shown.current = text.length;
          repaint((n) => n + 1);
        }
        return;
      }
      typing.current = true;
    }
    let frame = 0;
    let previous = performance.now();
    const tick = () => {
      const now = performance.now();
      const next = nextShown(shown.current ?? 0, text.length, (now - previous) / 1000, charsPerSecond);
      previous = now;
      shown.current = next;
      repaint((n) => n + 1);
      if (next >= text.length) {
        typing.current = false;
        return;
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [text, live, reducedMotion, charsPerSecond, turnId]);

  return shownSlice(text, shown.current ?? text.length);
}
