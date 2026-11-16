import suiUrl from "@/assets/sui.png";
import { cn } from "@/lib/utils";

/**
 * Suijin brand mark — Sui's face, small, beside the wordmark.
 * Compact = picture only (collapsed rail); full = picture + wordmark.
 */
export function BrandMark({
  compact = false,
  className,
}: {
  compact?: boolean;
  className?: string;
}) {
  if (compact) {
    return (
      <img
        src={suiUrl}
        alt="Suijin"
        data-brand-mark="compact"
        className={cn("size-8 shrink-0", className)}
      />
    );
  }

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <img src={suiUrl} alt="" aria-hidden="true" className="size-7 shrink-0" />
      <span className="font-heading text-lg font-semibold tracking-tight text-foreground">Suijin</span>
    </div>
  );
}
