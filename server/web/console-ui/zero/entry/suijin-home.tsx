/**
 * Suijin Home — the new-engagement landing.
 *
 * Ported from 0's native new-thread screen (0sec-labs/0, packages/web/src/console/conversation.tsx
 * @ 78d453c0, Apache-2.0 OR MIT): the backdrop, the layout wrappers, the context row, the composer
 * card, the gradient hairline, the textarea + overlay and the controls row all keep 0's markup and
 * class names, and every control is one of 0's prebuilt components (Backdrop, Button, Tooltip,
 * DropdownMenu, ModelCatalog, ModelIcon, LoadingDots).
 *
 * What Suijin changes is marked `suijin:` — content and wiring only:
 *   - 0's project selector (top context row) becomes the engagement Mode
 *   - 0's autonomy selector (the "YOLO" control in the composer) becomes Permissions
 *   - 0's model catalog is fed from Suijin's own runtime + model list
 *   - submit hands the prompt to the console's create-at-submit path; it creates nothing itself
 *   - the composer is larger; the copy, the palette and the suggestions are Suijin's
 */
import { useEffect, useLayoutEffect, useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import {
  ArrowUp,
  Bug,
  ChevronDown,
  ClipboardCheck,
  FileText,
  Flag,
  Flame,
  History,
  Plus,
  Radar,
  Settings,
  ShieldCheck,
  Stethoscope,
  Zap,
  X,
} from "lucide-react";
import { Backdrop } from "@zero/ui/backdrops";
import { useWebBackground } from "@zero/lib/web-appearance";
import { Button } from "@zero/components/ui/button";
import { Tooltip } from "@zero/components/ui/tooltip";
import { DropdownMenu } from "@zero/components/ui/dropdown-menu";
import { ModelCatalog, type CatalogModel, type CatalogProvider } from "@zero/components/model-picker";
import { ModelIcon } from "@zero/components/provider-icon";
import { LoadingDots } from "@zero/console/loading-state";
import {
  HERO_DRAFT_KEY,
  HERO_MAX_FILES,
  HERO_MAX_TEXT,
  isHeroMode,
  isHeroPermissions,
  type HeroMode,
  type HeroPermissions,
  type HeroSubmitMessage,
  type HeroToChild,
} from "./protocol";

/* ── suijin: content ─────────────────────────────────────────────────────────────────────── */

/** The engagement inbox takes uncompressed, unencrypted text only (mirrors the server's upload rule). */
const ACCEPT = ".md,.txt,.json,.yaml,.yml,.xml,.csv,.html,.conf,.ini,.log,.scope,text/*";
const ACCEPT_EXTENSIONS = ACCEPT.split(",").filter((entry) => entry.startsWith("."));
const TEXTUAL_TYPES = ["application/json", "application/xml", "application/yaml", "application/x-yaml", "application/toml", "application/javascript", "application/x-sh"];
/** 1,000,000 UTF-8 bytes, the server's cap. */
const MAX_FILE_BYTES = 1_000_000;
const MIN_COMPOSER_HEIGHT = 128;
const MAX_COMPOSER_HEIGHT = 360;
/** How long the console has to confirm a session before the text is handed back (creation can take a few seconds). */
const ACCEPT_TIMEOUT_MS = 30_000;

interface Choice<T extends string> {
  id: T;
  label: string;
  hint: string;
  icon: typeof Radar;
}

/** Suijin's autonomy levels — the slot 0 gives its own autonomy selector in the composer. */
const PERMISSIONS: Choice<HeroPermissions>[] = [
  { id: "recon", label: "Recon", icon: Radar, hint: "Passive and read-only. Nothing that changes state on the target." },
  { id: "exploitation", label: "Exploitation", icon: Zap, hint: "Safe-harbour testing: trigger the bug, capture proof, no destruction." },
  { id: "maximum", label: "Maximum", icon: Flame, hint: "Everything except denial of service." },
];

/** What the engagement is for — the slot 0 gives the project selector above the composer. */
const MODES: Choice<HeroMode>[] = [
  { id: "bbp", label: "BBP", icon: Bug, hint: "Paid-program rules. Stricter — skip findings too small to submit." },
  { id: "vdp", label: "VDP", icon: ShieldCheck, hint: "The same rules as BBP; smaller findings are worth recording." },
  { id: "ctf", label: "CTF", icon: Flag, hint: "Hunt flags. Everything is dummy data, so it runs free." },
  { id: "pentest", label: "Pentest", icon: ClipboardCheck, hint: "Needs an uploaded scope document before it starts." },
];

const SUGGESTIONS: { label: string; hint: string; prompt: string; icon: typeof Radar }[] = [
  {
    label: "Check Suijin is healthy",
    hint: "Providers, models, tools, the knowledge graph and the exploit cataloguer",
    prompt:
      "Run the Suijin doctor: check providers, models, tools, the knowledge graph and the exploit cataloguer. Report anything broken and how to fix it.",
    icon: Stethoscope,
  },
  {
    label: "Explain my top finding",
    hint: "Business impact, payability, and what the write-up is missing",
    prompt:
      "Open my highest-severity finding and explain it: what the bug is in plain language, the business impact, whether a program would pay for it, the chance of acceptance, and what the write-up is missing.",
    icon: Bug,
  },
  {
    label: "Recon a target",
    hint: "Passive recon — type the target after the prompt",
    prompt: "Run passive recon on ",
    icon: Radar,
  },
];

/* ── suijin: Suijin's own API (the frame is same-origin with the console) ──────────────────── */

interface RuntimeResponse {
  runtime?: { providerId?: string; model?: string };
  effort?: string;
}
interface AvailableModel {
  value: string;
  label: string;
  provider: string;
  providerLabel: string;
  supported: boolean;
}
interface AvailableModelsResponse {
  models?: AvailableModel[];
  providers?: { key: string; label: string }[];
}
interface Catalog {
  models: CatalogModel[];
  providers: CatalogProvider[];
  /** `provider:model` → display label, from the model list. */
  labels: Map<string, string>;
}
const EMPTY_CATALOG: Catalog = { models: [], providers: [], labels: new Map() };

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`.trim());
  return (await response.json()) as T;
}

/** Suijin's model list keys models as `provider:model`; 0's catalog wants the bare model id per provider. */
const stripProvider = (value: string, provider: string) => (value.startsWith(`${provider}:`) ? value.slice(provider.length + 1) : value);
/** `deepseek/deepseek-v4.1-flash:free` → `deepseek-v4.1-flash:free`: the vendor half is already the provider icon. */
const displayName = (id: string) => id.slice(id.lastIndexOf("/") + 1) || id;

const readDraft = () => {
  try {
    return sessionStorage.getItem(HERO_DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
};

const isTextual = (file: File) => {
  const name = file.name.toLowerCase();
  const dot = name.lastIndexOf(".");
  const extension = dot >= 0 ? name.slice(dot) : "";
  return file.type.startsWith("text/") || TEXTUAL_TYPES.includes(file.type) || ACCEPT_EXTENSIONS.includes(extension);
};

/* ── 0's two trigger styles, verbatim ────────────────────────────────────────────────────── */

/** Plain-text context trigger (components/backend-connection-picker.tsx, GHOST_TRIGGER): no fill, no border. */
const GHOST_TRIGGER =
  "-m-1 inline-flex max-sm:min-h-10 max-sm:px-2 min-w-0 max-w-64 items-center gap-1 rounded-[6px] border-[0.5px] border-transparent p-1 text-sm font-medium tracking-[-0.01em] [&_svg:first-of-type]:translate-y-[0.5px] text-muted-foreground-1 outline-none transition-colors duration-100 hover:text-foreground focus-visible:outline-ring-inset data-[popup-open]:text-foreground disabled:opacity-40";
/** Composer control trigger (the model / autonomy buttons in Conversation's controls row). */
const CONTROL_TRIGGER = "h-8 max-w-72 gap-1.5 px-2 text-[13px] font-[450] text-muted-foreground-1 hover:text-foreground";

/** The option list both selectors share. */
function Choices<T extends string>({ label, value, options, onChange, disabled, className }: { label: string; value: T; options: Choice<T>[]; onChange: (next: T) => void; disabled?: boolean; className?: string }) {
  return (
    <DropdownMenu.Content side="bottom" align="start" sideOffset={4} collisionPadding={12} className={`w-72 max-w-[calc(100vw-24px)] font-sans ${className ?? ""}`}>
      <DropdownMenu.Label>{label}</DropdownMenu.Label>
      {options.map((option) => {
        const Icon = option.icon;
        return (
          <DropdownMenu.Item key={option.id} selected={option.id === value} disabled={disabled} onClick={() => onChange(option.id)} className="items-start py-1.5">
            <Icon aria-hidden="true" strokeWidth={1} className="mt-0.5 size-3.5 shrink-0 text-icon-muted" />
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-[450] tracking-[-0.01em]">{option.label}</span>
              <span className="block text-xs leading-4 text-muted-foreground-1/90">{option.hint}</span>
            </span>
          </DropdownMenu.Item>
        );
      })}
    </DropdownMenu.Content>
  );
}

/** 0's context selector, in 0's own context row: icon, label, chevron; plain text. */
function ContextSelector<T extends string>({ label, value, options, onChange, disabled }: { label: string; value: T; options: Choice<T>[]; onChange: (next: T) => void; disabled?: boolean }) {
  const selected = options.find((option) => option.id === value) ?? options[0];
  const SelectedIcon = selected.icon;
  return (
    <div className="flex min-w-0 shrink-0 items-center gap-1">
      <DropdownMenu>
        <DropdownMenu.Trigger aria-label={label} disabled={disabled} className={GHOST_TRIGGER}>
          <SelectedIcon aria-hidden="true" className="size-3.5 shrink-0" strokeWidth={1} />
          <span className="truncate">{selected.label}</span>
          <ChevronDown aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground-3" strokeWidth={1.5} />
        </DropdownMenu.Trigger>
        <Choices label={label} value={value} options={options} onChange={onChange} disabled={disabled} />
      </DropdownMenu>
    </div>
  );
}

export function SuijinHome() {
  const [background] = useWebBackground();
  const [draft, setDraft] = useState(readDraft);
  const [files, setFiles] = useState<File[]>([]);
  const [permissions, setPermissions] = useState<HeroPermissions>("exploitation");
  const [mode, setMode] = useState<HeroMode>("vdp");
  const [busy, setBusy] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  /** The submission waiting for the console's acknowledgment; the send control shows it as in flight. */
  const [submitting, setSubmitting] = useState<HeroSubmitMessage | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [runtime, setRuntime] = useState<RuntimeResponse | null>(null);
  const [catalog, setCatalog] = useState<Catalog>(EMPTY_CATALOG);
  const [catalogPending, setCatalogPending] = useState(true);
  const [catalogError, setCatalogError] = useState<string | undefined>();
  const [modelError, setModelError] = useState<string | undefined>();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  /* ── console bridge ─────────────────────────────────────────────────────────────────── */

  useEffect(() => {
    const onMessage = (event: MessageEvent<unknown>) => {
      if (event.origin !== window.location.origin || event.source !== window.parent) return;
      const data = event.data as Partial<HeroToChild> | null;
      if (!data || typeof data !== "object") return;
      if (data.type === "suijin:hero:accepted") {
        // A session exists for the prompt now: clear the frame so nothing is sent twice.
        setSubmitting(null);
        setDraft("");
        setFiles([]);
        setNotice(null);
        return;
      }
      if (data.type === "suijin:hero:rejected") {
        // Nothing was created: the prompt and its files stay exactly where they were.
        setSubmitting(null);
        setNotice(typeof data.reason === "string" && data.reason ? data.reason : "The engagement didn't start. Try again.");
        return;
      }
      if (data.type !== "suijin:hero:state") return;
      if (isHeroPermissions(data.permissions)) setPermissions(data.permissions);
      if (isHeroMode(data.mode)) setMode(data.mode);
      setBusy(Boolean(data.busy));
      setReadOnly(Boolean(data.readOnly));
    };
    window.addEventListener("message", onMessage);
    window.parent.postMessage({ type: "suijin:hero:ready" }, window.location.origin);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    if (!submitting) return;
    const timer = window.setTimeout(() => {
      // suijin: never eat what the operator typed — if the console didn't take it, hand it back.
      setDraft((current) => current || submitting.text);
      setSubmitting(null);
      setNotice("The console didn't take that. Try again.");
    }, ACCEPT_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [submitting]);

  useEffect(() => {
    try {
      if (draft) sessionStorage.setItem(HERO_DRAFT_KEY, draft);
      else sessionStorage.removeItem(HERO_DRAFT_KEY);
    } catch {
      /* private mode: the draft simply doesn't survive a reload */
    }
  }, [draft]);

  /* ── suijin: model + effort for the next engagement ─────────────────────────────────── */

  useEffect(() => {
    let alive = true;
    requestJson<RuntimeResponse>("/api/console/runtime")
      .then((next) => {
        if (alive) setRuntime(next);
      })
      .catch(() => {});
    requestJson<AvailableModelsResponse>("/api/console/available-models")
      .then((data) => {
        if (!alive) return;
        const available = data.models ?? [];
        const models = available
          .filter((model) => model.supported !== false)
          .map((model) => ({ id: stripProvider(model.value, model.provider), provider: model.provider }));
        const labels = new Map(available.map((model) => [`${model.provider}:${stripProvider(model.value, model.provider)}`, model.label]));
        setCatalog({ models, providers: (data.providers ?? []).map((provider) => ({ id: provider.key, label: provider.label })), labels });
        setCatalogPending(false);
      })
      .catch((error: unknown) => {
        if (!alive) return;
        setCatalogPending(false);
        setCatalogError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      alive = false;
    };
  }, []);

  const pickModel = async (model: CatalogModel) => {
    setModelError(undefined);
    try {
      const next = await requestJson<RuntimeResponse>("/api/console/runtime", {
        method: "PATCH",
        body: JSON.stringify({ providerId: model.provider, model: model.id }),
      });
      // The endpoint answers `{ok, runtime: {runtime, effort}}`; the read endpoint answers `{runtime, effort}`.
      setRuntime(next.runtime ?? next);
      refocusComposer();
    } catch {
      setModelError(`Couldn't switch to ${model.id}. It stays on the previous model.`);
    }
  };

  /* ── typing ─────────────────────────────────────────────────────────────────────────── */

  useEffect(() => {
    const focus = () => {
      if (!document.activeElement || document.activeElement === document.body) textarea.current?.focus({ preventScroll: true });
    };
    focus();
    window.addEventListener("focus", focus);
    return () => window.removeEventListener("focus", focus);
  }, []);

  // suijin: 0's composer autosizes between 64 and 224px; Suijin's composer is larger, so it sizes itself.
  useLayoutEffect(() => {
    const node = textarea.current;
    if (!node) return;
    node.style.height = "auto";
    const height = Math.min(Math.max(node.scrollHeight, MIN_COMPOSER_HEIGHT), MAX_COMPOSER_HEIGHT);
    node.style.height = `${height}px`;
    node.style.overflowY = node.scrollHeight > MAX_COMPOSER_HEIGHT ? "auto" : "hidden";
  }, [draft]);

  /** After a menu choice, typing should just continue: Base UI returns focus to the trigger button. */
  const refocusComposer = () => {
    window.setTimeout(() => textarea.current?.focus({ preventScroll: true }), 0);
  };
  /** The console remembers a selection the moment it is made (it used to, with inline selects). */
  const announce = (nextPermissions: HeroPermissions, nextMode: HeroMode) => {
    window.parent.postMessage({ type: "suijin:hero:select", permissions: nextPermissions, mode: nextMode }, window.location.origin);
  };
  const chooseMode = (next: HeroMode) => {
    setMode(next);
    announce(permissions, next);
    refocusComposer();
  };
  const choosePermissions = (next: HeroPermissions) => {
    setPermissions(next);
    announce(next, mode);
    refocusComposer();
  };

  const needsScope = mode === "pentest" && files.length === 0;
  const closed = busy || readOnly;
  const canSend = draft.trim().length > 0 && !closed && !submitting && !needsScope;

  const submit = () => {
    if (!canSend) return;
    const message: HeroSubmitMessage = { type: "suijin:hero:submit", text: draft.trim().slice(0, HERO_MAX_TEXT), permissions, mode, files };
    window.parent.postMessage(message, window.location.origin);
    setSubmitting(message);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // IME composition (CJK input) commits with Enter — never send on that keystroke.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  const addFiles = (incoming: File[]) => {
    const accepted: File[] = [];
    let skipped = 0;
    for (const file of incoming) {
      if (isTextual(file) && file.size > 0 && file.size <= MAX_FILE_BYTES) accepted.push(file);
      else skipped += 1;
    }
    setFiles((currentFiles) => [...currentFiles, ...accepted].slice(0, HERO_MAX_FILES));
    setNotice(skipped ? `${skipped} file${skipped === 1 ? "" : "s"} skipped. The inbox takes uncompressed text files up to 1 MB.` : null);
  };

  const current = runtime?.runtime;
  const modelKey = current?.providerId && current.model ? `${current.providerId}:${current.model}` : "";
  const modelLabel = (modelKey && catalog.labels.get(modelKey)) || current?.model || "";
  const effort = runtime?.effort && runtime.effort !== "off" ? runtime.effort : "";
  const PermissionsIcon = (PERMISSIONS.find((option) => option.id === permissions) ?? PERMISSIONS[0]).icon;
  const chooseSuggestion = (prompt: string) => {
    setDraft(prompt);
    requestAnimationFrame(() => {
      const node = textarea.current;
      if (!node) return;
      node.focus();
      node.setSelectionRange(prompt.length, prompt.length);
    });
  };
  const openHistory = () => window.parent.postMessage({ type: "suijin:hero:history" }, window.location.origin);

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    addFiles(Array.from(event.dataTransfer.files));
  };

  /* ── layout: 0's Home wrappers ─────────────────────────────────────────────────────── */

  return (
    <div className="relative flex min-h-0 flex-1 flex-col" aria-label="Home">
      <Backdrop kind={background} />
      <div className="relative h-full overflow-y-auto overscroll-contain">
        <section className="relative flex min-h-full items-center justify-center px-6 py-10">
          {/* suijin: 0's column is 712px; Suijin's composer is the whole surface */}
          <div className="relative flex w-full min-w-0 max-w-[min(1120px,100%)] flex-col pb-[6dvh]">
            {/* 0's context row: plain-text selectors above the composer */}
            <div className="flex min-h-8 min-w-0 items-center justify-between gap-3 pb-1.5 pl-4 pr-4 text-muted-foreground">
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <ContextSelector label="Mode" value={mode} options={MODES} onChange={chooseMode} disabled={closed} />
              </div>
              {needsScope && (
                <span role="status" className="shrink-0 text-xs text-warning">
                  Pentest needs a scope document — attach one with +
                </span>
              )}
            </div>

            {/* 0's composer card */}
            <div
              onDrop={onDrop}
              onDragOver={(event) => {
                event.preventDefault();
                if (!dragging) setDragging(true);
              }}
              onDragEnter={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
              }}
              data-dragging={dragging || undefined}
              className="@container/composer relative flex flex-col rounded-[var(--radius-card,8px)] bg-card p-4 shadow-[var(--shadow-composer)]"
            >
              <span
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 rounded-[inherit] border-[0.5px] border-transparent [background:linear-gradient(180deg,var(--border-2),var(--border-1))] [mask:linear-gradient(#000,#000)_padding-box_exclude,linear-gradient(#000,#000)_border-box]"
              />
              {dragging && (
                <div
                  role="status"
                  className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center rounded-[inherit] border border-primary bg-card/90 text-[13px] font-[450] text-foreground"
                >
                  <FileText aria-hidden="true" strokeWidth={1.5} className="mr-2 size-3.5 text-primary-ink" />
                  Drop files to attach
                </div>
              )}
              {files.length > 0 && (
                <div aria-label="Attached files" className="relative mb-3 flex min-w-0 flex-wrap gap-1.5">
                  {files.map((file, index) => (
                    <span
                      key={`${file.name}-${index}`}
                      className="inline-flex max-w-64 min-w-0 items-center gap-1.5 rounded-md border-[0.5px] border-border-2 bg-secondary/40 py-1 pl-2 pr-1 text-xs text-muted-foreground-1"
                    >
                      <FileText aria-hidden="true" strokeWidth={1.5} className="size-3.5 shrink-0 text-icon-muted" />
                      <span className="truncate">{file.name}</span>
                      <button
                        type="button"
                        aria-label={`Remove ${file.name}`}
                        disabled={closed}
                        onClick={() => setFiles((currentFiles) => currentFiles.filter((_, position) => position !== index))}
                        className="inline-flex size-5 shrink-0 items-center justify-center rounded outline-none transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-ring-inset disabled:opacity-40"
                      >
                        <X aria-hidden="true" className="size-3" strokeWidth={1.5} />
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <div className="relative">
                <textarea
                  data-composer-textarea=""
                  data-suijin-home-textarea=""
                  autoFocus
                  rows={4}
                  autoComplete="off"
                  data-1p-ignore
                  data-lpignore="true"
                  ref={textarea}
                  aria-label="What should we hack today?"
                  className="block w-full resize-none overscroll-contain scroll-fade-y bg-transparent p-1 text-[16px] font-[450] leading-[1.35] tracking-[-0.014em] [--sf-size:32px] outline-none focus:outline-none focus-visible:outline-none supports-[animation-timeline:scroll()]:[scrollbar-width:none]"
                  style={{ minHeight: MIN_COMPOSER_HEIGHT, maxHeight: MAX_COMPOSER_HEIGHT }}
                  value={draft}
                  disabled={closed}
                  onChange={(event) => {
                    setDraft(event.target.value.slice(0, HERO_MAX_TEXT));
                    setNotice(null);
                  }}
                  onKeyDown={onKeyDown}
                />
                {!draft && !closed && (
                  <span
                    aria-hidden="true"
                    className="pointer-events-none absolute left-1 top-1 text-[16px] font-[450] leading-[1.35] tracking-[-0.014em] text-muted-foreground-2"
                  >
                    What should we hack today?
                    <span className="text-muted-foreground-3 @max-md/composer:hidden">, or drop a scope file</span>
                  </span>
                )}
              </div>

              {/* 0's controls row: + · autonomy · model · send */}
              <div className="-mx-1 -mb-1 flex items-center gap-2 pt-1">
                <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
                  <input
                    ref={fileInput}
                    type="file"
                    multiple
                    hidden
                    accept={ACCEPT}
                    data-suijin-home-file-input=""
                    onChange={(event) => {
                      addFiles(Array.from(event.target.files ?? []));
                      event.target.value = "";
                    }}
                  />
                  <Tooltip content="Attach files">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Attach files"
                      data-suijin-home-attach=""
                      disabled={closed}
                      onClick={() => fileInput.current?.click()}
                      className="text-icon-muted"
                    >
                      <Plus className="size-4" strokeWidth={1.5} />
                    </Button>
                  </Tooltip>
                  <DropdownMenu>
                    <Tooltip content="Permissions">
                      <DropdownMenu.Trigger aria-label="Permissions" data-suijin-home-permissions="" disabled={closed}>
                        <Button type="button" variant="ghost" size="sm" className={CONTROL_TRIGGER}>
                          <PermissionsIcon aria-hidden="true" strokeWidth={1.5} className="size-3.5 shrink-0" />
                          <span className="truncate">{(PERMISSIONS.find((option) => option.id === permissions) ?? PERMISSIONS[0]).label}</span>
                          <ChevronDown className="size-4 shrink-0 text-icon-muted" strokeWidth={1.5} />
                        </Button>
                      </DropdownMenu.Trigger>
                    </Tooltip>
                    <Choices label="Permissions" value={permissions} options={PERMISSIONS} onChange={choosePermissions} disabled={closed} />
                  </DropdownMenu>
                  <DropdownMenu onOpenChange={(open) => open && setModelError(undefined)}>
                    <Tooltip content="Choose model">
                      <DropdownMenu.Trigger aria-label="Choose model" data-suijin-home-model="" disabled={closed || !modelLabel}>
                        <Button type="button" variant="ghost" size="sm" className={CONTROL_TRIGGER}>
                          {modelLabel && (
                            <ModelIcon modelId={current?.model ?? ""} providerId={current?.providerId ?? ""} className="size-4 shrink-0 text-icon-muted" />
                          )}
                          <span className="truncate">{catalogPending && !modelLabel ? "Loading" : displayName(modelLabel)}</span>
                          {effort && <span className="shrink-0 text-muted-foreground-2">· {effort}</span>}
                          <ChevronDown className="size-4 shrink-0 text-icon-muted" strokeWidth={1.5} />
                        </Button>
                      </DropdownMenu.Trigger>
                    </Tooltip>
                    <DropdownMenu.Content
                      side="bottom"
                      align="start"
                      sideOffset={4}
                      collisionPadding={12}
                      className="max-w-[calc(100vw-24px)] max-h-[min(360px,var(--available-height,calc(100dvh-24px)))] overscroll-contain p-1 [&>.overflow-y-auto]:flex-1 [&>.overflow-y-auto]:scroll-py-1 flex w-[26rem] min-w-0 flex-col overflow-hidden font-sans"
                    >
                      {modelError && (
                        <p role="alert" className="px-2 py-1.5 text-xs text-destructive">
                          {modelError}
                        </p>
                      )}
                      <ModelCatalog
                        models={catalog.models}
                        providers={catalog.providers}
                        selected={{ providerId: current?.providerId, model: current?.model }}
                        backendId="suijin"
                        onSelect={(model) => void pickModel(model)}
                        pending={catalogPending}
                        error={catalogError ? `Could not load models (${catalogError}).` : undefined}
                        connections={catalog.providers.map((provider) => ({ id: provider.id, label: provider.label, connected: true }))}
                        emptyMessage="No models available. Open Model settings to connect a provider."
                        footer={
                          <DropdownMenu.Item
                            icon={<Settings className="size-3.5 shrink-0 text-icon-muted" strokeWidth={1.5} />}
                            className="text-[13px] font-[450] tracking-[-0.01em]"
                            onClick={() => window.parent.postMessage({ type: "suijin:hero:navigate", to: "/models" }, window.location.origin)}
                          >
                            Model settings
                          </DropdownMenu.Item>
                        }
                      />
                    </DropdownMenu.Content>
                  </DropdownMenu>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span hidden={draft.length < 30000} className={`text-xs ${draft.length >= HERO_MAX_TEXT ? "text-destructive" : "text-muted-foreground"}`}>
                    {draft.length.toLocaleString()} / {HERO_MAX_TEXT.toLocaleString()}
                  </span>
                  <Tooltip content="Past chats">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      data-suijin-home-history=""
                      onClick={openHistory}
                      className="h-8 gap-1.5 px-2 text-[13px] font-[450] text-muted-foreground-1 hover:text-foreground"
                    >
                      <History className="size-3.5" strokeWidth={1.5} />
                      <span>History</span>
                    </Button>
                  </Tooltip>
                  <Tooltip content="Hack it. Shift+Enter for a new line." shortcut="Enter">
                    <Button
                      type="button"
                      size="sm"
                      data-suijin-home-submit=""
                      aria-label="Hack it"
                      aria-busy={Boolean(submitting)}
                      disabled={!canSend}
                      onClick={submit}
                      className="h-8 gap-1.5 px-3 text-[13px] font-[450]"
                    >
                      {submitting ? <LoadingDots className="scale-75" /> : <span>Hack it</span>}
                      <ArrowUp className="size-3.5" strokeWidth={1.5} />
                    </Button>
                  </Tooltip>
                </div>
              </div>
            </div>

            {/* 0's suggestion row */}
            <div aria-label="Suggested prompts" className="flex w-full flex-wrap items-center justify-center gap-2 pt-5">
              {SUGGESTIONS.map((suggestion) => {
                const Icon = suggestion.icon;
                return (
                  <Tooltip key={suggestion.label} content={suggestion.hint}>
                    <Button
                      type="button"
                      variant="chip"
                      size="sm"
                      data-suijin-home-suggestion={suggestion.label}
                      disabled={closed}
                      className="max-w-full !px-2.5"
                      onClick={() => chooseSuggestion(suggestion.prompt)}
                    >
                      <Icon aria-hidden="true" strokeWidth={1} className="shrink-0 text-icon-muted" />
                      <span className="truncate">{suggestion.label}</span>
                    </Button>
                  </Tooltip>
                );
              })}
            </div>
            {notice && (
              <p role="status" data-suijin-home-notice="" className="pt-3 text-center text-xs text-muted-foreground-1">
                {notice}
              </p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
