import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Check, ChevronRight, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { maskSecrets, maskSecretText, parseJsonText, summarizeJson, toJsonText } from "./json-pretty";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

/** Rows drawn under one node; the rest are counted, so a 50,000-row tool output cannot freeze the chat. */
const MAX_CHILDREN = 200
/** Containers at least this deep start collapsed. */
const COLLAPSE_AT_DEPTH = 3
/** A container with at least this many children starts collapsed. */
const COLLAPSE_AT_SIZE = 40

const isContainer = (value: unknown): value is Json[] | { [key: string]: Json } =>
  typeof value === "object" && value !== null

function entriesOf(value: Json[] | { [key: string]: Json }): [string, Json][] {
  if (Array.isArray(value)) return value.map((item, index) => [String(index), item])
  return Object.entries(value) as [string, Json][]
}

function Scalar({ value }: { value: Json }) {
  if (value === null) return <span className="json-null">null</span>
  if (typeof value === "boolean") return <span className="json-boolean">{String(value)}</span>
  if (typeof value === "number") return <span className="json-number">{String(value)}</span>
  return <span className="json-string">{JSON.stringify(value)}</span>
}

interface NodeProps {
  name: string | null
  value: Json
  path: string
  last: boolean
  depth: number
  collapsed: Set<string>
  onToggle: (path: string) => void
}

function JsonNode({ name, value, path, last, depth, collapsed, onToggle }: NodeProps) {
  const open = !collapsed.has(path)
  const comma = last ? "" : ","
  const key = (
    <>
      {name !== null && (
        <>
          <span className="json-key">{JSON.stringify(name)}</span>
          <span className="json-punct">: </span>
        </>
      )}
    </>
  )
  if (!isContainer(value)) {
    return (
      <div className="json-row" data-json-depth={depth}>
        <span className="json-toggle" />
        {key}
        <Scalar value={value} />
        {comma && <span className="json-punct">{comma}</span>}
      </div>
    )
  }
  const entries = entriesOf(value)
  const bracket = Array.isArray(value) ? ["[", "]"] : ["{", "}"]
  const summary = Array.isArray(value)
    ? entries.length ? `${entries.length} ${entries.length === 1 ? "item" : "items"}` : ""
    : entries.length ? `${entries.length} ${entries.length === 1 ? "key" : "keys"}` : ""
  if (!entries.length) {
    return (
      <div className="json-row" data-json-depth={depth}>
        <span className="json-toggle" />
        {key}
        <span className="json-punct">{bracket.join("")}</span>
        {comma && <span className="json-punct">{comma}</span>}
      </div>
    )
  }
  const shown = entries.slice(0, MAX_CHILDREN)
  return (
    <div className="json-node" data-json-open={open ? "" : undefined}>
      <div className="json-row" data-json-depth={depth}>
        <button
          type="button"
          className="json-toggle"
          aria-expanded={open}
          aria-label={`${open ? "Collapse" : "Expand"} ${name === null ? "root" : name}`}
          onClick={() => onToggle(path)}
        >
          <ChevronRight aria-hidden="true" className={cn("size-3 transition-transform", open && "rotate-90")} />
        </button>
        {key}
        <button type="button" className="json-fold" aria-expanded={open} onClick={() => onToggle(path)}>
          <span className="json-punct">{bracket[0]}</span>
          {!open && <span className="json-muted"> {summary} </span>}
          {!open && <span className="json-punct">{bracket[1]}</span>}
        </button>
        {!open && comma && <span className="json-punct">{comma}</span>}
      </div>
      {open && (
        <>
          <div className="json-children">
            {shown.map(([child, item], index) => (
              <JsonNode
                key={child}
                name={child}
                value={item}
                path={`${path}.${child}`}
                last={index === entries.length - 1}
                depth={depth + 1}
                collapsed={collapsed}
                onToggle={onToggle}
              />
            ))}
            {entries.length > shown.length && (
              <div className="json-row json-muted" data-json-depth={depth + 1}>
                <span className="json-toggle" />
                … {entries.length - shown.length} more {Array.isArray(value) ? "items" : "keys"} (copy to see them all)
              </div>
            )}
          </div>
          <div className="json-row json-punct" data-json-depth={depth}>
            <span className="json-toggle" />
            {bracket[1]}
            {comma && <span className="json-punct">{comma}</span>}
          </div>
        </>
      )}
    </div>
  )
}

/** Paths of the containers that start collapsed. */
function initiallyCollapsed(root: Json, path = "$", depth = 0, out = new Set<string>()): Set<string> {
  if (!isContainer(root)) return out
  const entries = entriesOf(root)
  if (entries.length && (depth >= COLLAPSE_AT_DEPTH || entries.length >= COLLAPSE_AT_SIZE)) out.add(path)
  for (const [name, value] of entries.slice(0, MAX_CHILDREN)) {
    if (isContainer(value)) initiallyCollapsed(value, `${path}.${name}`, depth + 1, out)
  }
  return out
}

export interface JsonViewProps {
  /** A value already parsed. */
  value?: unknown
  /** Text a tool printed: shown as a tree when it carries JSON, as preformatted text otherwise. */
  text?: string
  /** Start with every container collapsed. */
  defaultCollapsed?: boolean
  className?: string
  /** The most the block grows before it scrolls. */
  maxHeight?: number
  /** Hide the toolbar (a dense table cell). */
  bare?: boolean
  /** The block's accessible name. */
  label?: string
}

/**
 * JSON, readable: a collapsible tree with keys, strings, numbers, booleans and null in their own
 * colours, a copy button that copies the pretty-printed value, and secrets masked on the screen.
 */
export function JsonView({ value, text, defaultCollapsed = false, className, maxHeight = 360, bare = false, label }: JsonViewProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const [copied, setCopied] = useState(false)
  const [wrap, setWrap] = useState(false)

  const parsed = useMemo(() => {
    if (value !== undefined) return { value: maskSecrets(value), before: "", after: "" }
    const source = typeof text === "string" ? text : ""
    const found = parseJsonText(source)
    if (!found) return null
    return { value: maskSecrets(found.value), before: source.slice(0, found.start).trim(), after: source.slice(found.end).trim() }
  }, [value, text])

  // The first render decides what starts collapsed; `defaultCollapsed` collapses everything else.
  useEffect(() => {
    if (!parsed || defaultCollapsed) return
    setCollapsed(initiallyCollapsed(parsed.value as Json))
  }, [parsed, defaultCollapsed])

  const summary = parsed ? summarizeJson(parsed.value) : null
  const size = parsed ? toJsonText(parsed.value).length : (text ?? "").length
  const allPaths = useMemo(() => {
    const paths = new Set<string>()
    if (!parsed) return paths
    const walk = (node: Json, path: string) => {
      if (!isContainer(node)) return
      const entries = entriesOf(node)
      if (entries.length) paths.add(path)
      for (const [name, child] of entries.slice(0, MAX_CHILDREN)) if (isContainer(child)) walk(child, `${path}.${name}`)
    }
    walk(parsed.value as Json, "$")
    return paths
  }, [parsed])

  const toggle = (path: string) =>
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })

  const copy = async () => {
    const content = parsed ? toJsonText(parsed.value) : (text ?? "")
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1400)
    } catch {
      /* clipboard unavailable (permissions, insecure context): nothing to show */
    }
  }

  if (!parsed) {
    const body = maskSecretText(typeof text === "string" ? text : JSON.stringify(value) ?? "")
    if (!bare && !body.trim()) return null
    return (
      <pre data-json-view="" data-json-kind="text" aria-label={label} className={cn("json-plain", className)} style={{ maxHeight }}>
        {body}
      </pre>
    )
  }

  const rootCollapsed = collapsed.has("$")
  return (
    <div data-json-view="" data-json-kind="tree" aria-label={label} className={cn("json-view", className)}>
      {!bare && (
        <div className="json-toolbar">
          <span className="json-label">JSON</span>
          <span className="json-muted">
            {summary?.label} · {size.toLocaleString()} {size === 1 ? "char" : "chars"}
          </span>
          <span className="flex-1" />
          <Tooltip content="Collapse or expand every container">
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={rootCollapsed ? "Expand all" : "Collapse all"}
              onClick={() => setCollapsed(rootCollapsed ? new Set() : new Set(allPaths))}
            >
              <span className="font-mono text-[10px] leading-none">{rootCollapsed ? "[+]" : "[-]"}</span>
            </Button>
          </Tooltip>
          <Tooltip content={wrap ? "Long values on one line" : "Wrap long values"}>
            <Button type="button" variant="ghost" size="icon-xs" aria-label={wrap ? "Stop wrapping" : "Wrap"} aria-pressed={wrap} onClick={() => setWrap((current) => !current)}>
              <span className="font-mono text-[10px] leading-none">↵</span>
            </Button>
          </Tooltip>
          <Tooltip content={copied ? "Copied" : "Copy the formatted JSON"}>
            <Button type="button" variant="ghost" size="icon-xs" aria-label="Copy JSON" onClick={() => void copy()}>
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
            </Button>
          </Tooltip>
        </div>
      )}
      {(parsed.before || parsed.after) && (
        <div className="json-around">
          {parsed.before && <span>{parsed.before}</span>}
          {parsed.after && <span className="json-muted"> {parsed.after}</span>}
        </div>
      )}
      <div data-json-wrap={wrap ? "" : undefined} className="json-scroll" style={{ maxHeight: bare ? undefined : maxHeight }}>
        <JsonNode name={null} value={parsed.value as Json} path="$" last depth={0} collapsed={collapsed} onToggle={toggle} />
      </div>
    </div>
  )
}

/** Whatever a value is, readable: JSON as a tree, text as text. */
export function JsonOrText({ value, ...rest }: Omit<JsonViewProps, "value" | "text"> & { value: unknown }) {
  if (typeof value === "string") return <JsonView text={value} {...rest} />
  if (value === undefined || value === null) return null
  return <JsonView value={value} {...rest} />
}

/** A scalar for a table cell: a short, coloured rendering; containers become a summary. */
export function JsonScalar({ value }: { value: unknown }): ReactNode {
  if (typeof value === "string") {
    const masked = maskSecretText(value)
    return masked.length > 80 ? <span className="json-string">{JSON.stringify(masked.slice(0, 80))}…</span> : <span className="json-string">{JSON.stringify(masked)}</span>
  }
  if (isContainer(value)) {
    const summary = summarizeJson(value)
    return <span className="json-muted">{Array.isArray(value) ? `[${summary.label}]` : `{${summary.label}}`}</span>
  }
  return <Scalar value={value as Json} />
}
