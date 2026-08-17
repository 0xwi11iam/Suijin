import type { Snapshot } from "./types"

const base = ""

export async function fetchOverview(): Promise<Snapshot> {
  const r = await fetch(`${base}/api/overview`)
  if (!r.ok) throw new Error(`overview ${r.status}`)
  return r.json()
}

export async function fetchKbSearch(q: string, limit = 5): Promise<{ result: string }> {
  const r = await fetch(`/api/kb/search?q=${encodeURIComponent(q)}&limit=${limit}`)
  if (!r.ok) throw new Error(`kb search ${r.status}`)
  return r.json()
}

export async function fetchReport(path: string): Promise<{ content: string }> {
  const r = await fetch(`/api/report?path=${encodeURIComponent(path)}`)
  if (!r.ok) throw new Error(`report ${r.status}`)
  return r.json()
}

export async function fetchSession(file: string): Promise<Record<string, unknown>> {
  const r = await fetch(`/api/session?file=${encodeURIComponent(file)}`)
  if (!r.ok) throw new Error(`session ${r.status}`)
  return r.json()
}

export async function fetchConfig(): Promise<Record<string, unknown>> {
  const r = await fetch("/api/config")
  if (!r.ok) throw new Error(`config ${r.status}`)
  return r.json()
}

/** Subscribe to the backend SSE snapshot stream. Returns an unsubscribe fn. */
export function subscribe(
  onSnap: (s: Snapshot) => void,
  onErr?: () => void
): () => void {
  const es = new EventSource(`${base}/api/events`)
  es.onmessage = (ev) => {
    try {
      onSnap(JSON.parse(ev.data))
    } catch {
      /* skip malformed frame */
    }
  }
  es.onerror = () => onErr?.()
  return () => es.close()
}
