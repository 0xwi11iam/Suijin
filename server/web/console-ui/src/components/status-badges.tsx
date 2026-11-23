import { Badge } from "@/components/ui/badge";
import type { FindingReviewGate, FindingWorkflowPhase, FindingWorkflowStatus } from "@/types";

function formatWorkflowLabel(value: FindingWorkflowStatus) {
  return value.replaceAll("_", " ");
}

export function SeverityBadge({ severity }: { severity: string }) {
  const normalized = severity.toLowerCase();
  const variant =
    normalized === "critical" || normalized === "high"
      ? "danger"
      : normalized === "medium"
        ? "warning"
        : normalized === "low"
          ? "info"
          : "neutral";

  return <Badge variant={variant}>{severity}</Badge>;
}

/** CVSS is the badge operators read first. The number is the finding; the
 * color follows the AI-defined severity word (never a calculator). When no
 * word was stated, the numeric band is the only signal left. */
export function CvssBadge({
  score,
  severity,
}: {
  score?: number | null;
  severity?: string | null;
}) {
  const sev = String(severity || "").toLowerCase();
  const numeric = typeof score === "number" && Number.isFinite(score) ? score : null;
  const computedBand =
    numeric === null
      ? ""
      : numeric >= 9
        ? "critical"
        : numeric >= 7
          ? "high"
          : numeric >= 4
            ? "medium"
            : numeric > 0
              ? "low"
              : "info";
  const band = sev || computedBand;
  const variant =
    band === "critical" || band === "high"
      ? "danger"
      : band === "medium"
        ? "warning"
        : band === "low"
          ? "info"
          : "neutral";
  const label = numeric === null ? (sev || "unscored") : `CVSS ${numeric.toFixed(1)}`;
  return <Badge variant={variant}>{label}</Badge>;
}

/** Short EXP identifier from the exploit catalog, e.g. EXP-002. */
export function ExpIdBadge({ id }: { id?: string | null }) {
  if (!id) return null;
  return (
    <Badge variant="outline" className="font-mono text-[10px] tracking-wide">
      {id}
    </Badge>
  );
}

/** A finding's state is a dot, not a paragraph. Color is the signal; the
 * label is a tooltip. */
export function FindingStatusDot({ status }: { status?: string | null }) {
  const normalized = String(status || "new").toLowerCase();
  const color =
    normalized === "confirmed" || normalized === "accepted" || normalized === "complete" || normalized === "completed"
      ? "bg-emerald-500"
      : normalized === "failed" || normalized === "error" || normalized === "critical"
        ? "bg-red-500"
        : normalized === "running" || normalized === "claiming"
          ? "bg-amber-500"
          : "bg-muted-foreground/50";
  return (
    <span
      role="img"
      aria-label={`Status: ${normalized}`}
      title={`Status: ${normalized.replaceAll("_", " ")}`}
      className={`inline-block size-2 shrink-0 rounded-full ${color}`}
    />
  );
}

export function StatusBadge({
  value,
}: {
  value: string;
}) {
  const normalized = value.toLowerCase();
  const variant =
    normalized === "accepted" || normalized === "complete" || normalized === "completed"
      ? "success"
      : normalized === "suppressed"
        ? "warning"
        : normalized === "running" || normalized === "claiming"
          ? "accent"
          : normalized === "failed" || normalized === "critical" || normalized === "error"
            ? "danger"
            : normalized === "sleeping"
              ? "warning"
            : "neutral";

  return <Badge variant={variant}>{value}</Badge>;
}

export function PhaseBadge({
  value,
}: {
  value: FindingWorkflowPhase;
}) {
  const variant =
    value === "done"
      ? "success"
      : value === "cancelled" || value === "blocked"
        ? "danger"
        : value === "in_progress"
          ? "accent"
          : "neutral";

  return <Badge variant={variant}>{formatWorkflowLabel(value)}</Badge>;
}

export function ReviewBadge({
  value,
}: {
  value: FindingReviewGate;
}) {
  if (value === "none") return null;

  return (
    <Badge variant={value === "human_review" ? "warning" : "accent"}>
      {formatWorkflowLabel(value)}
    </Badge>
  );
}
