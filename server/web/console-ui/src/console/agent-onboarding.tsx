import suiPortraitUrl from "@/assets/sui.png";

/** The empty chat state greets with Sui, not a setup prompt. */
export function AgentOnboarding() {
  return <img src={suiPortraitUrl} alt="Sui" className="size-20 rounded-full shadow-lg ring-1 ring-teal-500/40" />;
}

export const setupPrompt = "";
