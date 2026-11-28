/**
 * HeroFrame — hosts the new-engagement landing (`zero/home.html`) inside the console.
 *
 * The landing is 0's native new-thread screen carried in `zero/` (upstream source, its own
 * stylesheet). It runs in a same-origin frame so that stylesheet can never reach the console's
 * chat, and the two talk only through the messages defined in `zero/entry/protocol.ts`. Everything
 * that drives an engagement — session creation, the inbox upload, navigation — stays here in the
 * console; the frame only collects the prompt.
 *
 * Both sides check `event.origin` and `event.source`, and every submission is shape-checked before
 * it reaches the create-at-submit path.
 */
import { useEffect, useRef, useState, type MutableRefObject } from "react";
import {
  HERO_DRAFT_KEY,
  isHeroKind,
  isHeroMode,
  isHeroPermissions,
  parseHeroSubmit,
  type HeroKind,
  type HeroToChild,
  type HeroMode,
  type HeroPermissions,
  type HeroSubmitMessage,
  type HeroToParent,
} from "../../zero/entry/protocol";

export type { HeroKind, HeroMode, HeroPermissions, HeroSubmitMessage };

/**
 * How the console reports the outcome of a submission. `accept` once a session exists for it;
 * `reject` when nothing was created — the landing then keeps the operator's text and files.
 */
export interface HeroBridge {
  accept: () => void;
  reject: (reason: string) => void;
}

/** Served from the console's own `dist` (a second Vite entry), so it is always same-origin. */
const HERO_SRC = "/zero/home.html";

export interface HeroFrameProps {
  permissions: HeroPermissions;
  mode: HeroMode;
  kind: HeroKind;
  busy: boolean;
  readOnly: boolean;
  /** The console takes the submission into its create-at-submit path. Must set the draft synchronously. */
  onSubmit: (submission: HeroSubmitMessage) => void;
  /** The console reports the outcome through this handle (see HeroBridge). */
  bridge: MutableRefObject<HeroBridge | null>;
  /** "History" opens the console's own chat rail. */
  onHistory: () => void;
  /** The frame has no router of its own; in-app links come through as paths. */
  onNavigate: (to: string) => void;
  /** A selector changed on the landing. The console remembers it for the next landing. */
  onSelect: (selection: { permissions: HeroPermissions; mode: HeroMode; kind: HeroKind }) => void;
}

const isInternalPath = (to: unknown): to is string => typeof to === "string" && /^\/[a-z0-9/_-]*$/i.test(to);

export function HeroFrame({ permissions, mode, kind, busy, readOnly, onSubmit, onHistory, onNavigate, onSelect, bridge }: HeroFrameProps) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  // Latest callbacks + selectors, read at message time: the listener is installed once, so a
  // message never reaches a stale closure or a stale default.
  const latest = useRef({ onSubmit, onHistory, onNavigate, onSelect, permissions, mode, kind });
  latest.current = { onSubmit, onHistory, onNavigate, onSelect, permissions, mode, kind };

  useEffect(() => {
    const onMessage = (event: MessageEvent<unknown>) => {
      const source = frame.current?.contentWindow;
      if (!source || event.source !== source || event.origin !== window.location.origin) return;
      const data = event.data as Partial<HeroToParent> | null;
      if (!data || typeof data !== "object") return;
      switch (data.type) {
        case "suijin:hero:ready":
          setReady(true);
          break;
        case "suijin:hero:submit": {
          const submission = parseHeroSubmit(data, { permissions: latest.current.permissions, mode: latest.current.mode, kind: latest.current.kind });
          if (!submission) return;
          latest.current.onSubmit(submission);
          // The console reports the outcome through the bridge once it knows it.
          break;
        }
        case "suijin:hero:history":
          latest.current.onHistory();
          break;
        case "suijin:hero:navigate":
          if (isInternalPath(data.to)) latest.current.onNavigate(data.to);
          break;
        case "suijin:hero:select":
          if (isHeroPermissions(data.permissions) && isHeroMode(data.mode) && isHeroKind(data.kind))
            latest.current.onSelect({ permissions: data.permissions, mode: data.mode, kind: data.kind });
          break;
        default:
          break;
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    if (!ready) return;
    frame.current?.contentWindow?.postMessage({ type: "suijin:hero:state", permissions, mode, kind, busy, readOnly }, window.location.origin);
  }, [ready, permissions, mode, kind, busy, readOnly]);

  useEffect(() => {
    const send = (message: HeroToChild) => frame.current?.contentWindow?.postMessage(message, window.location.origin);
    bridge.current = {
      // The console navigates away the moment it accepts, and the frame may be gone before a message
      // lands — so the console clears the stored draft itself (same origin, same tab).
      accept: () => {
        try {
          sessionStorage.removeItem(HERO_DRAFT_KEY);
        } catch {
          /* private mode */
        }
        send({ type: "suijin:hero:accepted" });
      },
      reject: (reason) => send({ type: "suijin:hero:rejected", reason }),
    };
    return () => {
      bridge.current = null;
    };
  }, [bridge]);

  // Absolutely positioned inside a flex column: the frame can never collapse to zero height, and
  // its own document scrolls, so the console's layout is untouched.
  return (
    <div data-suijin-hero-host="" className="relative min-h-0 flex-1">
      <iframe
        ref={frame}
        src={HERO_SRC}
        title="New chat"
        data-suijin-hero-frame=""
        className="absolute inset-0 block h-full w-full border-0 bg-transparent"
      />
    </div>
  );
}
