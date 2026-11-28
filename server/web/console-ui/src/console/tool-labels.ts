/** Curated tool names, served by /api/console/tools (the plugins work's casing map):
 *  the chat rows say "Used CRT.sh", not "Used crtsh". Fetched once, cached for the tab. */

export type ToolLabels = Record<string, string>;

let cache: ToolLabels | null = null;
let pending: Promise<ToolLabels> | null = null;

export function toolLabels(): Promise<ToolLabels> {
  if (cache) return Promise.resolve(cache);
  if (!pending) {
    pending = fetch("/api/console/tools", { headers: { accept: "application/json" } })
      .then(res => (res.ok ? res.json() : { tools: [] }))
      .then((data: { tools?: { name: string; label?: string }[] }) => {
        const map: ToolLabels = {};
        for (const tool of data.tools ?? []) if (tool.label) map[tool.name.toLowerCase()] = tool.label;
        cache = map;
        return map;
      })
      .catch(() => ({}));
  }
  return pending;
}
