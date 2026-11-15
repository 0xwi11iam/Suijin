import suiUrl from "@/assets/sui.png";

/** The hero: Sui's face above the wordmark. */
export function SuiHero({ className }: { className?: string }) {
  return (
    <div className={`flex flex-col items-center gap-3 ${className ?? ""}`}>
      <img src={suiUrl} alt="Sui" className="w-36 rounded-2xl" />
      <span className="font-heading text-2xl font-semibold tracking-tight text-foreground">Suijin</span>
    </div>
  );
}
