/**
 * The pieces every operations page is built from: panels, stats, state badges, relative time,
 * meters, table cells, a JSON dialog, and a one-line flash for the result of an action.
 */
import { useEffect, useState, type ComponentProps, type ReactNode } from "react";
import { AlertCircle, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { TableCell, TableHead } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { JsonView } from "@/console/json-view";

export type Tone = "emerald" | "amber" | "red" | "sky" | "violet" | "neutral";

const DOT: Record<Tone, string> = {
  emerald: "bg-emerald-500", amber: "bg-amber-500", red: "bg-red-500", sky: "bg-sky-500", violet: "bg-violet-500", neutral: "bg-muted-foreground/50",
};
const BADGE: Record<Tone, "success" | "warning" | "danger" | "info" | "accent" | "neutral"> = {
  emerald: "success", amber: "warning", red: "danger", sky: "info", violet: "accent", neutral: "neutral",
};

/** A panel with a quiet header: a title, a line of explanation, its controls on the right. */
export function Panel({ title, description, actions, children, className, flush }: {
  title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; flush?: boolean;
}) {
  return (
    <section data-panel="" className={cn("min-w-0 overflow-hidden rounded-[var(--radius-panel)] border border-border/60 bg-card", className)}>
      {(title || actions) && (
        <header className="flex min-h-11 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border/60 px-4 py-2.5">
          <div className="min-w-0">
            {title && <h2 data-panel-title="" className="truncate text-sm font-medium text-foreground">{title}</h2>}
            {description && <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn("min-w-0", !flush && "p-4")}>{children}</div>
    </section>
  );
}

/** One number, what it counts, and — when it matters — whether it is good news. */
export function Stat({ label, value, hint, tone = "neutral", className }: {
  label: string; value: ReactNode; hint?: ReactNode; tone?: Tone; className?: string;
}) {
  return (
    <div data-stat="" className={cn("min-w-0 rounded-[var(--radius-control,8px)] border border-border/60 bg-card px-3.5 py-3", className)}>
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Dot tone={tone} /> <span className="truncate">{label}</span>
      </div>
      <div className="mt-1.5 truncate text-xl font-medium tabular-nums tracking-tight text-foreground">{value}</div>
      {hint && <div className="mt-1 truncate text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function Dot({ tone = "neutral", pulse = false, className }: { tone?: Tone; pulse?: boolean; className?: string }) {
  return <span aria-hidden="true" data-dot={tone} className={cn("inline-block size-2 shrink-0 rounded-full", DOT[tone], pulse && "animate-pulse", className)} />;
}

export function StateBadge({ tone, children, className, pulse }: { tone: Tone; children: ReactNode; className?: string; pulse?: boolean }) {
  return <Badge variant={BADGE[tone]} className={cn("gap-1.5 font-normal", className)}><Dot tone={tone} pulse={pulse} />{children}</Badge>;
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-[12px] text-foreground", className)}>{children}</span>;
}

export function Th({ children, className, ...props }: ComponentProps<"th">) {
  return <TableHead className={cn("h-9 whitespace-nowrap px-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground first:pl-4 last:pr-4", className)} {...props}>{children}</TableHead>;
}

export function Td({ children, className, ...props }: ComponentProps<"td">) {
  return <TableCell className={cn("px-3 py-2 align-middle text-[13px] text-foreground first:pl-4 last:pr-4", className)} {...props}>{children}</TableCell>;
}

export function EmptyRow({ colSpan, children }: { colSpan: number; children: ReactNode }) {
  return (
    <tr>
      <TableCell colSpan={colSpan} className="px-4 py-8 text-center text-sm text-muted-foreground">{children}</TableCell>
    </tr>
  );
}

/** A bar for a value between 0 and 1 (or a part of a whole). */
export function Meter({ value, max = 1, tone = "sky", label, className }: { value: number; max?: number; tone?: Tone; label?: string; className?: string }) {
  const ratio = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <div data-meter="" className={cn("h-1.5 w-full overflow-hidden rounded-full bg-muted", className)} role="presentation">
      <div className={cn("h-full rounded-full transition-[width] duration-500 motion-reduce:transition-none", DOT[tone])} style={{ width: `${ratio * 100}%` }} />
      {label && <span className="sr-only">{label}</span>}
    </div>
  );
}

const ago = (ms: number): string => {
  if (ms < 0) return "in the future";
  const s = Math.round(ms / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m ago`;
  return `${Math.floor(h / 24)}d ago`;
};

/** "4s ago" that keeps counting. */
export function RelTime({ seconds, epoch, className }: { seconds?: number | null; epoch?: number | null; className?: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (epoch !== undefined && epoch !== null) return <span className={cn("whitespace-nowrap tabular-nums text-muted-foreground", className)}>{ago(Date.now() - epoch * 1000)}</span>;
  if (seconds === undefined || seconds === null) return <span className={cn("whitespace-nowrap text-muted-foreground", className)}>—</span>;
  return <span className={cn("whitespace-nowrap tabular-nums text-muted-foreground", className)}>{ago(seconds * 1000)}</span>;
}

export function fmtBytes(n: number): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

export function fmtDur(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function fmtTime(isoOrEpoch?: string | number | null): string {
  if (!isoOrEpoch) return "—";
  const date = typeof isoOrEpoch === "number" ? new Date(isoOrEpoch * 1000) : new Date(isoOrEpoch);
  if (Number.isNaN(date.getTime())) return String(isoOrEpoch);
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ErrorNote({ error, children }: { error: unknown; children?: ReactNode }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-[var(--radius-control,8px)] border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0 break-words">{children ?? String(error instanceof Error ? error.message : error)}</div>
    </div>
  );
}

/** Anything, opened in a dialog as a readable JSON tree. */
export function JsonDialog({ open, onOpenChange, title, value, text, description }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; value?: unknown; text?: string; description?: string;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm">{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <JsonView value={value} text={text} maxHeight={560} />
      </DialogContent>
    </Dialog>
  );
}

// ── flash: what an action did, said once for the whole page ───────────────────────────────

export interface Flash {
  message: string;
  kind: "status" | "error";
  at: number;
}
type FlashListener = (flash: Flash | null) => void;
const flashListeners = new Set<FlashListener>();
let flashTimer: number | undefined;

/** Tell the page what an action did. Any panel can say it; the page shows it in one place. */
export function flash(message: string, kind: "status" | "error" = "status"): void {
  const item: Flash = { message, kind, at: Date.now() };
  for (const listener of [...flashListeners]) listener(item);
  window.clearTimeout(flashTimer);
  flashTimer = window.setTimeout(() => {
    for (const listener of [...flashListeners]) listener(null);
  }, 6000);
}

/** The same, in the shape the panels already use. */
export function useFlash() {
  return { show: flash, clear: () => { for (const listener of [...flashListeners]) listener(null); } };
}

/** One line under the page header. It never covers a control — the app's floating notice does. */
export function FlashBar() {
  const [item, setItem] = useState<Flash | null>(null);
  useEffect(() => {
    flashListeners.add(setItem);
    return () => {
      flashListeners.delete(setItem);
    };
  }, []);
  if (!item) return null;
  return (
    <div
      role={item.kind === "error" ? "alert" : "status"}
      data-flash={item.kind}
      className={cn(
        "rounded-[var(--radius-control,8px)] border px-3 py-2 text-sm",
        item.kind === "error" ? "border-destructive/30 bg-destructive/10 text-destructive" : "border-border/60 bg-card text-foreground",
      )}
    >
      <span className="inline-flex items-center gap-2">
        <Dot tone={item.kind === "error" ? "red" : "emerald"} />
        {item.message}
      </span>
    </div>
  );
}

export function DismissButton({ onClick, label = "Dismiss" }: { onClick: () => void; label?: string }) {
  return <Button type="button" variant="ghost" size="icon-xs" aria-label={label} onClick={onClick}><X className="size-3.5" /></Button>;
}

/** An action that cannot be undone asks first; the destructive ones ask for a word. */
export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel = "Confirm", tone = "default", typed, onConfirm, pending, children }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; description?: ReactNode;
  confirmLabel?: string; tone?: "default" | "danger" | "warning"; typed?: string; onConfirm: () => void; pending?: boolean; children?: ReactNode;
}) {
  const [word, setWord] = useState("");
  useEffect(() => { if (!open) setWord(""); }, [open]);
  const ready = !typed || word.trim().toLowerCase() === typed.toLowerCase();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="text-sm leading-6">{description}</DialogDescription>
        </DialogHeader>
        {typed && (
          <label className="block space-y-1.5 text-sm text-muted-foreground">
            <span>Type <Mono>{typed}</Mono> to continue</span>
            <Input value={word} onChange={(event) => setWord(event.target.value)} autoComplete="off" spellCheck={false} />
          </label>
        )}
        {children && <div className="min-w-0">{children}</div>}
        <DialogFooter>
          <Button type="button" variant="ghost" size="sm" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="button" size="sm" variant={tone === "danger" ? "destructive" : tone === "warning" ? "warning" : "default"} disabled={!ready || pending} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
