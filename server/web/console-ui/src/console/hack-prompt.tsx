import { useEffect, useState } from "react";

/** Rotating hero prompts — the front door asks differently every time. */
const PROMPTS = [
  "What would you like to hack today?",
  "Where are we hunting tonight?",
  "Pick a target. The engagement is warm.",
  "What should the engagement break first?",
  "Which surface are we probing today?",
  "Got scope? Let's hunt.",
  "Another box, another bounty?",
];

export function HackPrompt({ className }: { className?: string }) {
  const [index, setIndex] = useState(() => Math.floor(Math.random() * PROMPTS.length));
  useEffect(() => {
    const timer = setInterval(() => setIndex(value => (value + 1) % PROMPTS.length), 3_600_000);
    return () => clearInterval(timer);
  }, []);
  return <h2 key={index} className={className} style={{ animation: "hack-prompt-in .45s ease both" }}>{PROMPTS[index]}</h2>;
}
