/** The hero's centre is a quote — no mark, no wordmark (operator 2026-11-28:
 * "no more sui, just a big hero in the middle and its an inspirational hacking
 * related quote… from a bank of 50"). One per visit, never the same one twice
 * in a row, stored per tab so a reload keeps what you were looking at. */

const QUOTES: readonly string[] = [
  "What are we hacking today?",
  "Every target keeps one more secret.",
  "The front door is locked. Good.",
  "Trust nothing. Verify everything.",
  "Recon first. Bravado later.",
  "Every endpoint is a question.",
  "The best bugs hide in plain sight.",
  "Read the JavaScript. It always confesses.",
  "Cookies remember what you forget.",
  "Somewhere, an API trusts the client.",
  "Rate limits are suggestions.",
  "The 403 is not a wall. It is a door.",
  "Enumerate until boredom. Then again.",
  "Old endpoints never die. They get forgotten.",
  "Look where they stopped looking.",
  "robots.txt is a treasure map.",
  "Admin panels love forgotten subdomains.",
  "The session expired. They said it does.",
  "Every parameter has a story.",
  "Prove it, then report it.",
  "The target does not know what it exposes.",
  "Stay humble. Enumerate.",
  "In skepticism there is safety.",
  "The forgotten subdomain is the open window.",
  "Input is a suggestion. Output is the truth.",
  "One token, many doors.",
  "The IDOR is in the ID.",
  "Test like an attacker. Report like a professional.",
  "Curiosity is the payload.",
  "First you map. Then you move.",
  "Not all who 404 are lost.",
  "The backup file is still there.",
  "Debug endpoints are someone's oversight.",
  "Every header is an argument.",
  "Staging has production data. It always does.",
  "Trust the process, not the promise.",
  "Validation happens somewhere. Just not here.",
  "A bug unreported is a bug unfixed.",
  "Scope is a contract. Honor it.",
  "The graph remembers what the target forgot.",
  "Small diffs, big doors.",
  "Patience finds what scanners miss.",
  "Go where the docs don't.",
  "Everything speaks, if you probe it right.",
  "The response body never lies.",
  "Own the boring endpoints. That is where the bugs are.",
  "Map first. Hack second.",
  "Ask the quiet endpoint what it holds.",
  "Twenty minutes of recon saves twenty of noise.",
  "Finish the thought. Then finish the hunt.",
];

const LAST_KEY = "suijin-hero-quote";

/** The quote for this visit: random, never the previous one. */
export function heroQuote(): string {
  let last = -1;
  try { last = Number(sessionStorage.getItem(LAST_KEY) ?? -1); } catch { /* a fresh tab every time */ }
  let index = Math.floor(Math.random() * QUOTES.length);
  if (index === last && QUOTES.length > 1) index = (index + 1) % QUOTES.length;
  try { sessionStorage.setItem(LAST_KEY, String(index)); } catch { /* private mode: just show it */ }
  return QUOTES[index]!;
}
