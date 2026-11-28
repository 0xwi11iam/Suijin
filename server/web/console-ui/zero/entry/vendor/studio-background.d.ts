/**
 * Types for the asciify-engine "studio background" template (MIT, asciify.org), which ships as plain JavaScript.
 * The engine's own `StudioSettings` types the settings object, so a misspelled option fails the typecheck.
 */
import type { StudioSettings } from "asciify-engine/studio";

type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends readonly (infer U)[] ? U[] : T[K] extends object ? DeepPartial<T[K]> : T[K];
};

export interface StudioBackgroundOptions {
  /** Procedural source painted behind the render — `fluid`, `field`, … */
  pattern?: string;
  speed?: number;
  /** Variant of the pattern — `flow`, … */
  scene?: string;
  /** Partial studio settings; the engine fills every omitted field from its defaults. */
  settings?: DeepPartial<StudioSettings>;
}

/** Mounts a live background on `canvas`, sized from and hover-tracked on `host`. */
export function mountStudioBackground(
  host: HTMLElement,
  canvas: HTMLCanvasElement,
  options: StudioBackgroundOptions,
): { update(options: StudioBackgroundOptions): void; destroy(): void };

export function paintBackgroundSource(...args: unknown[]): unknown;
