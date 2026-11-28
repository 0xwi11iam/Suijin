/**
 * JSON, made readable: find the JSON in what a tool printed, pretty-print it, colour it, and keep
 * secrets off the screen. Pure functions only — the viewer lives in ./json-view.tsx.
 */

export type JsonToken =
  | { type: "key" | "string" | "number" | "boolean" | "null" | "punct" | "whitespace"; value: string };

export interface ParsedJson {
  value: unknown;
  /** Where the JSON sits in the original text, so a prefix and a trailing note can be kept. */
  start: number;
  end: number;
}

const isContainer = (value: unknown): value is Record<string, unknown> | unknown[] =>
  typeof value === "object" && value !== null && (Array.isArray(value) || Object.getPrototypeOf(value) === Object.prototype);

const tryParse = (text: string): unknown => {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
};

/** Index of the bracket that closes the one at `open`, skipping brackets inside strings. -1 when unbalanced. */
function matchingEnd(text: string, open: number): number {
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = open; i < text.length; i++) {
    const char = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') inString = true;
    else if (char === "{" || char === "[") depth++;
    else if (char === "}" || char === "]") {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

/**
 * The JSON container in `text`, wherever a tool put it: bare, in a fenced block, behind a prefix
 * such as `RESULT: `, or with a trailing note. Null when there is no JSON container — plain prose
 * and bare scalars are left exactly as they are.
 */
export function parseJsonText(text: string): ParsedJson | null {
  if (typeof text !== "string") return null;
  const leading = text.length - text.trimStart().length;
  const trimmed = text.trim();
  if (!trimmed) return null;

  const whole = tryParse(trimmed);
  if (isContainer(whole)) return { value: whole, start: leading, end: leading + trimmed.length };

  const fenced = /```(?:json)?\s*\n?([\s\S]*?)```/i.exec(trimmed);
  if (fenced) {
    const inner = fenced[1].trim();
    const value = tryParse(inner);
    if (isContainer(value)) {
      const start = leading + trimmed.indexOf(inner);
      return { value, start, end: start + inner.length };
    }
  }

  // JSON inside other text (a `RESULT:` prefix, a trailing note) counts only when the container is
  // the substance of the text. Source code is not JSON: `read("a")["rows"]` is a balanced, valid
  // array, and it turned a Python snippet into a one-item tree (2026-11-28).
  for (let i = 0; i < trimmed.length; i++) {
    if (trimmed[i] !== "{" && trimmed[i] !== "[") continue;
    const end = matchingEnd(trimmed, i);
    if (end < 0) continue;
    const inner = trimmed.slice(i, end + 1);
    if (inner.length < 12 || inner.length < trimmed.length * 0.3) continue;
    const value = tryParse(inner);
    if (isContainer(value)) return { value, start: leading + i, end: leading + end + 1 };
  }
  return null;
}

/** Two-space indentation, keys in the order they were given. */
export function toJsonText(value: unknown): string {
  const text = JSON.stringify(value, null, 2);
  return text === undefined ? String(value) : text;
}

/**
 * Splits pretty-printed JSON into colourable tokens. A string is a KEY when a colon follows it;
 * whitespace is kept as tokens so the pieces reassemble into the original text.
 */
export function tokenizeJson(source: string): JsonToken[] {
  const tokens: JsonToken[] = [];
  const isSpace = (char: string) => char === " " || char === "\n" || char === "\t" || char === "\r";
  let i = 0;
  while (i < source.length) {
    const char = source[i];
    if (char === '"') {
      let j = i + 1;
      while (j < source.length && source[j] !== '"') {
        if (source[j] === "\\") j++;
        j++;
      }
      j = Math.min(j + 1, source.length);
      let k = j;
      while (k < source.length && isSpace(source[k])) k++;
      tokens.push({ type: source[k] === ":" ? "key" : "string", value: source.slice(i, j) });
      i = j;
    } else if (char === "{" || char === "}" || char === "[" || char === "]" || char === ":" || char === ",") {
      tokens.push({ type: "punct", value: char });
      i++;
    } else if (char === "-" || char === "+" || char === "." || (char >= "0" && char <= "9")) {
      let j = i + 1;
      while (j < source.length && /[0-9eE+\-.]/.test(source[j])) j++;
      tokens.push({ type: "number", value: source.slice(i, j) });
      i = j;
    } else if (/[A-Za-z]/.test(char)) {
      let j = i + 1;
      while (j < source.length && /[A-Za-z]/.test(source[j])) j++;
      const word = source.slice(i, j);
      tokens.push({ type: word === "true" || word === "false" ? "boolean" : word === "null" ? "null" : "string", value: word });
      i = j;
    } else {
      let j = i + 1;
      while (j < source.length && isSpace(source[j])) j++;
      tokens.push({ type: "whitespace", value: source.slice(i, j) });
      i = j;
    }
  }
  return tokens;
}

/** The viewer's header line. */
export function summarizeJson(value: unknown): { kind: "object" | "array" | "scalar"; label: string } {
  if (Array.isArray(value)) return { kind: "array", label: value.length ? `${value.length} ${value.length === 1 ? "item" : "items"}` : "empty" };
  if (isContainer(value)) {
    const count = Object.keys(value).length;
    return { kind: "object", label: count ? `${count} ${count === 1 ? "key" : "keys"}` : "empty" };
  }
  return { kind: "scalar", label: value === null ? "null" : typeof value };
}

const MASK = "••••••";
/**
 * A key is a secret when its whole name, or its last `_`/`-` separated part, is a secret word:
 * `password`, `neo4j_password`, `api_key`. Words that merely contain "key" or "token" are settings
 * — `max_tokens`, `token_count`, `keyEnv`, `keyboard`, `model_key`. A bare lowercase `key` is a
 * name (a registry key such as `zai`); an upper-case or camel-case one (`KEY`, `apiKey`) is not.
 */
const SECRET_KEY =
  /^(?:.*[_-])?(?:api[-_]?key|apikey|access[-_]?token|refresh[-_]?token|id[-_]?token|auth[-_]?token|token|secret|client[-_]?secret|secret[-_]?key|password|passwd|pwd|credential|credentials|authorization|proxy[-_]?authorization|cookie|cookies|set[-_]?cookie|bearer|private[-_]?key|session[-_]?key|extraheaders|identity[-_]?headers)$/i;
const SECRET_KEY_EXACT = /^(KEY|Key)$/;
const SECRET_ENV_KEY = /^[A-Z0-9_]*(_KEY|_TOKEN|_SECRET|_PASSWORD|_PWD)$/;

/** Masks the secrets inside one string: bearer and basic headers, cookies, key=value credentials, JWTs. */
export function maskSecretText(text: string): string {
  return text
    .replace(/\b(Bearer|Basic|Token)\s+[A-Za-z0-9\-_=+.\/]{8,}/gi, `$1 ${MASK}`)
    .replace(/\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b/g, MASK)
    .replace(/(Cookie:\s*)([^\n]+)/gi, (_match, head: string, cookie: string) =>
      head + cookie.split(/;\s*/).map((part) => /^([^=\s]+)=/.test(part) ? `${part.slice(0, part.indexOf("="))}=${MASK}` : part).join("; "))
    .replace(/\b((?:x-)?api[-_]?key|access[-_]?token|refresh[-_]?token|id[-_]?token|client[-_]?secret|secret|password|passwd|authorization)\b(["']?\s*[:=]\s*["']?)((?!(?:Bearer|Basic|Token)\b)[^\s,"'}&\]]{6,})/gi,
      (_match, name: string, glue: string) => `${name}${glue}${MASK}`)
    .replace(/\bsk-[A-Za-z0-9_-]{12,}\b/g, MASK);
}

function maskKey(name: string): boolean {
  return SECRET_KEY.test(name) || SECRET_ENV_KEY.test(name) || SECRET_KEY_EXACT.test(name);
}

function maskValue(value: unknown, key: string, seen: WeakSet<object>): unknown {
  if (typeof value === "string") return maskKey(key) ? MASK : maskSecretText(value);
  if (typeof value !== "object" || value === null || seen.has(value)) return value;
  seen.add(value);
  if (Array.isArray(value)) return value.map((item) => maskValue(item, key, seen));
  const out: Record<string, unknown> = {};
  for (const [name, item] of Object.entries(value as Record<string, unknown>)) out[name] = maskValue(item, name, seen);
  return out;
}

/**
 * A copy of `value` with credential-shaped KEYS masked wherever they are nested, and with the
 * secrets inside strings masked (a bearer header, a cookie, a `key=value` credential, a JWT).
 * Settings that merely contain the word "token" or "key" — `max_tokens`, `model_key`, `keyEnv` —
 * are left alone: the mask matches credential names, not substrings.
 */
export function maskSecrets<T>(value: T): T {
  return maskValue(value, "", new WeakSet<object>()) as T;
}
