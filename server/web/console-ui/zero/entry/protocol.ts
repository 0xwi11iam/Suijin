/**
 * Messages between the console (parent document) and the Home landing (`zero/home.html`).
 *
 * Both documents are same-origin and both sides check `event.origin` AND `event.source` before
 * acting on a message, so another window can never start an engagement through this channel.
 * This module is deliberately dependency-free: it is the only file shared across the boundary.
 */

export type HeroPermissions = "recon" | "exploitation" | "maximum";
export type HeroMode = "bbp" | "vdp" | "ctf" | "pentest";
/** What a submission starts: a CHAT with Suijin (the default) or an ENGAGEMENT (a hunt). */
export type HeroKind = "chat" | "engagement";

export const HERO_PERMISSIONS: readonly HeroPermissions[] = ["recon", "exploitation", "maximum"];
export const HERO_MODES: readonly HeroMode[] = ["bbp", "vdp", "ctf", "pentest"];
export const HERO_KINDS: readonly HeroKind[] = ["chat", "engagement"];
/** Operator ruling (2026-11-27): the landing is a new chat unless the operator says engagement. */
export const HERO_DEFAULT_KIND: HeroKind = "chat";
/** Mirrors the composer's own cap, so a paste larger than the chat accepts is refused here too. */
export const HERO_MAX_TEXT = 32_000;
/** Session-storage key for an unsubmitted landing draft. Shared by both documents (same origin, same tab). */
export const HERO_DRAFT_KEY = "suijin-hero-draft";
export const HERO_MAX_FILES = 20;

/** Parent → child. Sent once the frame reports ready, and again whenever one of these changes. */
export interface HeroStateMessage {
  type: "suijin:hero:state";
  permissions: HeroPermissions;
  mode: HeroMode;
  kind: HeroKind;
  busy: boolean;
  readOnly: boolean;
}
/** Parent → child. A session now exists for the submission; the console owns the prompt. */
export interface HeroAcceptedMessage {
  type: "suijin:hero:accepted";
}
/** Parent → child. No session was created; the frame keeps the prompt and shows why. */
export interface HeroRejectedMessage {
  type: "suijin:hero:rejected";
  reason: string;
}
/** Child → parent. `files` are structured-cloned File objects (text scope documents for the engagement inbox). */
export interface HeroSubmitMessage {
  type: "suijin:hero:submit";
  text: string;
  permissions: HeroPermissions;
  mode: HeroMode;
  kind: HeroKind;
  files: File[];
}
export interface HeroReadyMessage {
  type: "suijin:hero:ready";
}
export interface HeroHistoryMessage {
  type: "suijin:hero:history";
}
/** Child → parent. A selector changed on the landing; the console remembers it for next time. */
export interface HeroSelectMessage {
  type: "suijin:hero:select";
  permissions: HeroPermissions;
  mode: HeroMode;
  kind: HeroKind;
}
export interface HeroNavigateMessage {
  type: "suijin:hero:navigate";
  to: string;
}

export type HeroToParent = HeroSubmitMessage | HeroReadyMessage | HeroHistoryMessage | HeroNavigateMessage | HeroSelectMessage;
export type HeroToChild = HeroStateMessage | HeroAcceptedMessage | HeroRejectedMessage;

export const isHeroPermissions = (value: unknown): value is HeroPermissions =>
  HERO_PERMISSIONS.includes(value as HeroPermissions);
export const isHeroMode = (value: unknown): value is HeroMode => HERO_MODES.includes(value as HeroMode);
export const isHeroKind = (value: unknown): value is HeroKind => HERO_KINDS.includes(value as HeroKind);

/**
 * The console never trusts the frame blindly: a submission is shape-checked and clamped before it
 * reaches the create-at-submit path. Unknown selectors fall back to the console's own current values.
 */
export function parseHeroSubmit(
  data: unknown,
  fallback: { permissions: HeroPermissions; mode: HeroMode; kind: HeroKind },
): HeroSubmitMessage | null {
  if (!data || typeof data !== "object") return null;
  const record = data as Record<string, unknown>;
  if (record.type !== "suijin:hero:submit" || typeof record.text !== "string") return null;
  const text = record.text.trim().slice(0, HERO_MAX_TEXT);
  if (!text) return null;
  const files = Array.isArray(record.files)
    ? record.files.filter((item): item is File => typeof File !== "undefined" && item instanceof File).slice(0, HERO_MAX_FILES)
    : [];
  return {
    type: "suijin:hero:submit",
    text,
    permissions: isHeroPermissions(record.permissions) ? record.permissions : fallback.permissions,
    mode: isHeroMode(record.mode) ? record.mode : fallback.mode,
    kind: isHeroKind(record.kind) ? record.kind : fallback.kind,
    files,
  };
}
