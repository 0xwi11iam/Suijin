import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Markmap } from "markmap-view";
import { ArrowLeft, Crosshair, RefreshCcw, Search, X } from "lucide-react";
import { useBackendApi } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { LoadingState } from "@/components/state-panel";

type GraphEntry = { category: string; rule: string; evidence: string; confidence?: number; severity?: string; verified?: boolean };
type GraphNode = {
  id: string; label: string; kind: string; host?: string; internal: boolean;
  entryCount: number; categories?: Record<string, number>; topRule?: string;
  severity?: string; entries: GraphEntry[]; color?: string; size?: number;
};
type GraphEdge = { source: string; target: string; ekind?: string };
type GraphPayload = {
  nodes: GraphNode[]; edges: GraphEdge[];
  stats: { nodes: number; edges: number; entries: number };
  missing?: boolean;
};

/** Markmap's data shape: `content` is HTML, children are nested. We build it
 * from the graph instead of markdown so every node keeps its graph id. */
type MarkNode = {
  content: string;
  children: MarkNode[];
  payload?: { color?: string; root?: boolean };
};

type Built = {
  root: MarkNode;
  nodeIndex: Map<string, MarkNode>;
  targetEntries: Map<string, GraphEntry[]>;
  targetVulns: Map<string, number>;
  /** leaf id -> the target it belongs to (search focuses the target). */
  ownerOf: Map<string, string>;
};

const LEGEND: { color: string; label: string }[] = [
  { color: "#14b8a6", label: "External targets" },
  { color: "#fb923c", label: "Internal targets" },
  { color: "#60a5fa", label: "Info" },
  { color: "#2dd4bf", label: "Endpoints" },
  { color: "#ef4444", label: "Vulnerabilities" },
  { color: "#94a3b8", label: "Unsorted" },
];

const BRANCH_COLOR: Record<string, string> = {
  "branch:info": "#60a5fa",
  "branch:endpoints": "#2dd4bf",
  "branch:vulns": "#ef4444",
};

const esc = (value: unknown) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

function buildTree(payload: GraphPayload): Built {
  const byId = new Map(payload.nodes.map(node => [node.id, node] as const));
  const nodeIndex = new Map<string, MarkNode>();
  const targetEntries = new Map<string, GraphEntry[]>();
  const targetVulns = new Map<string, number>();
  const ownerOf = new Map<string, string>();

  const targets = payload.nodes.filter(node => node.kind.startsWith("target"));
  const branches = ["info", "endpoints", "vulns"] as const;

  // Endpoint nodes (host-level intel) are linked to a target's /endpoints
  // or /info branch by an edge; the id alone does not say whose it is.
  const endpointTarget = new Map<string, string>();
  const branchRe = /^(target:.+)\/(info|endpoints|vulns)$/;
  for (const edge of payload.edges) {
    if (edge.ekind === "cross") continue;
    for (const [self, other] of [[edge.source, edge.target], [edge.target, edge.source]] as const) {
      const endpoint = byId.get(self);
      if (endpoint?.kind !== "endpoint") continue;
      const match = branchRe.exec(other);
      if (!match) continue;
      const current = endpointTarget.get(self);
      if (!current || match[2] === "endpoints") endpointTarget.set(self, match[1]!);
    }
  }

  const leavesFor = (targetId: string, branch: (typeof branches)[number]): GraphNode[] => {
    const prefix = `${targetId}/${branch}/leaf:`;
    const out = payload.nodes.filter(node => node.id.startsWith(prefix));
    if (branch === "endpoints") {
      for (const [endpointId, owner] of endpointTarget) {
        if (owner !== targetId) continue;
        const endpoint = byId.get(endpointId);
        if (endpoint) out.push(endpoint);
      }
    }
    return out;
  };

  const entriesOf = (leaves: GraphNode[]): GraphEntry[] => leaves.flatMap(leaf => leaf.entries ?? []);
  const branchLabel = (branch: string) =>
    branch === "info" ? "Info" : branch === "endpoints" ? "Endpoints" : "Vulnerabilities";

  const leafNode = (leaf: GraphNode, targetId: string, isVuln: boolean): MarkNode => {
    const entries = leaf.entries?.length ? leaf.entries : [{ category: "", rule: leaf.label || leaf.id, evidence: "" }];
    const content = entries.map(entry => {
      const severity = String(entry.severity || leaf.severity || "").trim();
      const cls = isVuln ? "mm-vuln" : "mm-leaf";
      return `<div class="${cls}" data-gid="${esc(leaf.id)}" data-target="${esc(targetId)}">`
        + (severity ? `<b class="mm-sev">${esc(severity)}</b> ` : "")
        + `${esc(entry.rule || leaf.label || leaf.id)}</div>`
        + (entry.evidence ? `<div class="mm-evidence">${esc(entry.evidence)}</div>` : "");
    }).join("");
    const node: MarkNode = {
      content,
      children: [],
      payload: { color: isVuln ? "#ef4444" : leaf.color || "#94a3b8" },
    };
    nodeIndex.set(leaf.id, node);
    ownerOf.set(leaf.id, targetId);
    return node;
  };

  const categories = [
    { id: "__ext__", kind: "target:external", label: byId.get("__ext__")?.label || "External targets", color: "#14b8a6" },
    { id: "__int__", kind: "target:internal", label: byId.get("__int__")?.label || "Internal targets", color: "#fb923c" },
    { id: "__unsorted__", kind: "", label: byId.get("__unsorted__")?.label || "Unsorted", color: "#94a3b8" },
  ];

  const categoryNodes: MarkNode[] = [];
  for (const category of categories) {
    const members = targets.filter(node => (category.kind ? node.kind === category.kind : !node.kind.startsWith("target:")));
    const targetNodes: MarkNode[] = [];
    for (const target of members) {
      const allEntries: GraphEntry[] = [];
      const branchNodes: MarkNode[] = [];
      for (const branch of branches) {
        const leaves = leavesFor(target.id, branch);
        const entries = entriesOf(leaves);
        allEntries.push(...entries);
        const isVuln = branch === "vulns";
        const node: MarkNode = {
          content: `<span class="mm-branch" data-gid="${esc(`${target.id}/${branch}`)}" data-target="${esc(target.id)}">${branchLabel(branch)}</span> <span class="${isVuln ? "mm-vuln-badge" : "mm-badge"}">${entries.length}</span>`,
          children: leaves.map(leaf => leafNode(leaf, target.id, isVuln)),
          payload: { color: BRANCH_COLOR[`branch:${branch}`] ?? "#94a3b8" },
        };
        nodeIndex.set(`${target.id}/${branch}`, node);
        ownerOf.set(`${target.id}/${branch}`, target.id);
        branchNodes.push(node);
      }
      targetEntries.set(target.id, allEntries);
      const vulns = entriesOf(leavesFor(target.id, "vulns")).length;
      targetVulns.set(target.id, vulns);
      const targetNode: MarkNode = {
        content: `<span class="mm-target" data-gid="${esc(target.id)}" data-target="${esc(target.id)}">${esc(target.label)}</span>`
          + (vulns ? ` <span class="mm-vuln-badge" title="${vulns} vulnerabilities">${vulns}</span>` : ""),
        children: branchNodes,
        payload: { color: category.color },
      };
      nodeIndex.set(target.id, targetNode);
      ownerOf.set(target.id, target.id);
      targetNodes.push(targetNode);
    }
    const categoryNode: MarkNode = {
      content: `<span class="mm-cat" data-gid="${esc(category.id)}">${esc(category.label)}</span> <span class="mm-badge">${members.length}</span>`,
      children: targetNodes,
      payload: { color: category.color },
    };
    nodeIndex.set(category.id, categoryNode);
    categoryNodes.push(categoryNode);
  }

  const root: MarkNode = {
    content: `<span class="mm-root" data-gid="__root__">MEMORY</span> <span class="mm-badge">${payload.stats.entries}</span>`,
    children: categoryNodes,
    payload: { color: "#64748b", root: true },
  };
  nodeIndex.set("__root__", root);
  return { root, nodeIndex, targetEntries, targetVulns, ownerOf };
}

/** Markmap options that keep the operator's dark, reading-first surface. */
const MAP_OPTIONS = {
  autoFit: false,
  duration: 220,
  fitRatio: 0.88,
  initialExpandLevel: 3,
  maxWidth: 460,
  maxInitialScale: 1.6,
  nodeMinHeight: 18,
  paddingX: 10,
  spacingHorizontal: 110,
  spacingVertical: 12,
  color: (node: any) => String(node?.payload?.color ?? "#64748b"),
  lineWidth: (node: any) => (node?.payload?.root ? 4 : 1.6),
} as const;

const MAP_CSS = `
.memory-markmap .markmap {
  --markmap-font: 500 12.5px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --markmap-text-color: #dbe4ee;
  --markmap-code-bg: #1e293b;
  --markmap-code-color: #7dd3fc;
  --markmap-circle-open-bg: #0f172a;
  --markmap-highlight-node-bg: #f59e0b33;
  --markmap-a-color: #38bdf8;
  --markmap-a-hover-color: #7dd3fc;
  width: 100%;
  height: 100%;
}
.memory-markmap .markmap-node > circle { fill: #0b0d10; stroke: #475569; }
.memory-markmap .markmap-link { stroke-opacity: .42; }
.memory-markmap .markmap-foreign { padding: 1px 2px; }
.mm-root { font-weight: 750; letter-spacing: .1em; font-size: 14px; color: #f8fafc; }
.mm-cat { font-weight: 650; letter-spacing: .02em; }
.mm-target { font-weight: 650; color: #e2e8f0; }
.mm-branch { font-weight: 600; color: #cbd5e1; }
.mm-leaf { color: #b6c2d2; }
.mm-vuln { color: #fecaca; }
.mm-sev { color: #f87171; text-transform: uppercase; font-size: 9.5px; letter-spacing: .06em; }
.mm-badge, .mm-vuln-badge {
  display: inline-block; border-radius: 999px; padding: 0 6px;
  font: 600 10px/16px ui-monospace, SFMono-Regular, Menlo, monospace;
  vertical-align: 1px;
}
.mm-badge { background: #1e293b; color: #93c5fd; }
.mm-vuln-badge { background: #7f1d1d; color: #fecaca; }
.mm-evidence {
  margin-top: 3px; color: #94a3b8; white-space: pre-wrap;
  font: 400 10.5px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
}
`;

export function MemoryMindmapPage() {
  const { webFetchJson } = useBackendApi();
  const navigate = useNavigate();
  const svgRef = useRef<SVGSVGElement>(null);
  const mmRef = useRef<Markmap | null>(null);
  const mmNodesRef = useRef<Map<string, any>>(new Map());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const graph = useQuery({
    queryKey: ["memory-graph"],
    queryFn: ({ signal }) => webFetchJson<GraphPayload>("/api/console/memory-graph", { signal }),
    refetchOnWindowFocus: false,
  });
  const built = useMemo(() => (graph.data ? buildTree(graph.data) : null), [graph.data]);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || !built) return;
    const mm = Markmap.create(svg, MAP_OPTIONS as any, built.root as any);
    mmRef.current = mm;
    // markmap CLONES the data we hand it and attaches `state` to the clones.
    // setHighlight/ensureVisible on our original nodes throws
    // `Cannot read properties of undefined (reading 'id')`, so keep a map of
    // the live internal nodes (keyed by the data-gid we rendered).
    const internal = new Map<string, any>();
    const index = (node: any) => {
      const match = /data-gid="([^"]+)"/.exec(String(node?.content ?? ""));
      if (match) internal.set(match[1]!, node);
      for (const child of node?.children ?? []) index(child);
    };
    if (mm.state.data) index(mm.state.data);
    mmNodesRef.current = internal;
    // A fit-all of 83 targets lands at ~0.3x and the text becomes 4px.
    // Start readable: center the root, then hold ~0.95x. The Fit button is
    // the explicit overview.
    const rootNode = internal.get("__root__");
    if (rootNode) {
      void mm.rescale(0.95)
        .then(() => mm.ensureVisible(rootNode, { left: 360, right: 120, top: 160, bottom: 120 }))
        .catch(() => {});
    } else {
      void mm.fit().catch(() => {});
    }
    // Exposed for headless verification (operator screenshot rule).
    (window as any).__memoryMarkmap = mm;
    const onClick = (event: MouseEvent) => {
      const el = (event.target as Element | null)?.closest?.("[data-target]") as HTMLElement | null;
      const targetId = el?.dataset.target;
      if (targetId) setSelectedId(targetId);
    };
    svg.addEventListener("click", onClick);
    return () => {
      svg.removeEventListener("click", onClick);
      mmRef.current = null;
      mm.destroy();
    };
  }, [built]);

  useEffect(() => {
    const mm = mmRef.current;
    if (!mm || !selectedId) return;
    const node = mmNodesRef.current.get(selectedId);
    if (node) void mm.setHighlight(node).catch(() => {});
  }, [selectedId]);

  const selected = useMemo(
    () => graph.data?.nodes.find(node => node.id === selectedId) ?? null,
    [graph.data, selectedId],
  );
  const selectedEntries = selected
    ? (selected.entries.length ? selected.entries : built?.targetEntries.get(selected.id) ?? [])
    : [];
  const groupedEntries = useMemo(() => {
    const groups = new Map<string, GraphEntry[]>();
    for (const entry of selectedEntries) {
      if (!groups.has(entry.category)) groups.set(entry.category, []);
      groups.get(entry.category)!.push(entry);
    }
    return [...groups.entries()];
  }, [selectedEntries]);
  const selectedVulns = selected ? built?.targetVulns.get(selected.id) ?? 0 : 0;

  const focus = (id: string) => {
    const mm = mmRef.current;
    const node = mmNodesRef.current.get(id);
    if (mm && node) {
      void mm.setHighlight(node).catch(() => {});
      void mm.ensureVisible(node, { left: 120, right: 120, top: 120, bottom: 120 }).catch(() => {});
    }
    setSelectedId(id);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (selectedId) setSelectedId(null);
        else navigate("/chats");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, navigate]);

  const matches = query.trim() && graph.data
    ? graph.data.nodes
        .filter(node => !node.id.startsWith("__")
          && !node.kind.startsWith("branch")
          && node.label.toLowerCase().includes(query.trim().toLowerCase()))
        .slice(0, 8)
    : [];
  const stats = graph.data?.stats;
  const extCount = graph.data?.nodes.filter(node => node.kind === "target:external").length ?? 0;
  const intCount = graph.data?.nodes.filter(node => node.kind === "target:internal").length ?? 0;

  return <div className="fixed inset-0 z-[100] bg-[#0b0d10] text-foreground">
    <style>{MAP_CSS}</style>
    <div className="memory-markmap absolute inset-0">
      <svg ref={svgRef} className="size-full" aria-label="Memory mindmap" />
    </div>

    {/* back + controls */}
    <div className="absolute left-4 top-4 z-10 flex flex-col gap-2">
      <Button variant="outline" size="sm" onClick={() => navigate("/chats")} className="bg-[#111418]/90"><ArrowLeft className="size-4" />Back</Button>
      <div className="flex gap-1.5">
        <Button variant="outline" size="icon-sm" title="Fit map" aria-label="Fit map" className="bg-[#111418]/90" onClick={() => void mmRef.current?.fit()}><Crosshair className="size-4" /></Button>
        <Button variant="outline" size="icon-sm" title="Reload memory" aria-label="Reload memory" className="bg-[#111418]/90" disabled={graph.isFetching} onClick={() => void graph.refetch()}><RefreshCcw className="size-4" /></Button>
      </div>
      <div className="relative w-64">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search targets…" aria-label="Search the memory map" className="h-9 bg-[#111418]/90 pl-9 text-sm" autoComplete="off" />
        {matches.length > 0 && <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-72 overflow-y-auto rounded-[var(--radius-menu,12px)] border border-border bg-[#111418] p-1 shadow-xl">
          {matches.map(match => <button key={match.id} type="button" onClick={() => { focus(built?.ownerOf.get(match.id) ?? match.id); setQuery(""); }}
            className="flex w-full items-center gap-2 rounded-[var(--radius-control,8px)] px-2.5 py-1.5 text-left text-sm hover:bg-muted/60">
            <span className="size-2 shrink-0 rounded-full" style={{ background: match.color ?? "#94a3b8" }} />
            <span className="min-w-0 flex-1 truncate">{match.label}</span>
            <span className="shrink-0 text-xs text-muted-foreground">{match.kind.startsWith("target") ? "Target" : "Entry"}</span>
          </button>)}
        </div>}
      </div>
    </div>

    {/* stats + legend */}
    {stats && <div className="absolute bottom-4 left-4 z-10 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="rounded-full bg-[#111418]/90 px-2.5 py-1 font-mono">{stats.entries} intel entries</span>
        <span className="rounded-full bg-[#111418]/90 px-2.5 py-1 font-mono">{stats.nodes} nodes</span>
        <span className="rounded-full bg-[#111418]/90 px-2.5 py-1 font-mono text-teal-300">{extCount} external</span>
        <span className="rounded-full bg-[#111418]/90 px-2.5 py-1 font-mono text-orange-300">{intCount} internal</span>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        {LEGEND.map(({ color, label }) => <span key={label} className="flex items-center gap-1.5"><span className="size-2.5 rounded-full" style={{ background: color }} />{label}</span>)}
      </div>
    </div>}

    {/* inspector */}
    {selected && <aside className="absolute bottom-4 right-4 top-4 z-20 flex w-[420px] max-w-[92vw] flex-col overflow-hidden rounded-[var(--radius-panel,16px)] border border-border bg-[#111418]/97 shadow-2xl">
      <div className="flex items-start gap-3 border-b border-border/60 p-4">
        <span className="mt-1 size-3 shrink-0 rounded-full" style={{ background: selected.color ?? "#94a3b8" }} />
        <div className="min-w-0 flex-1"><h2 className="break-all text-sm font-semibold" title={selected.id}>{selected.label}</h2>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{selected.kind.replace(":leaf", " entry").replaceAll(":", " · ")}</span>
            {selected.internal && <span className="rounded-full bg-orange-500/15 px-2 py-0.5 text-orange-300">internal</span>}
            {selected.severity && <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-red-300">{selected.severity}</span>}
            {selectedVulns > 0 && <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-red-300">{selectedVulns} vulns</span>}
            <span>{selectedEntries.length} intel entries</span>
          </p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close inspector" onClick={() => setSelectedId(null)}><X className="size-4" /></Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {selectedEntries.length === 0 && <p className="text-sm text-muted-foreground">No intel entries recorded for this node.</p>}
        {groupedEntries.map(([category, entries]) => <div key={category} className="mb-5">
          <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{category} <span className="font-mono">{entries.length}</span></h3>
          <ul className="space-y-3">
            {entries.map((entry, index) => <li key={index} className="rounded-[var(--radius-menu,12px)] border border-border/50 bg-muted/20 p-3">
              <p className="text-sm leading-5">{entry.rule}</p>
              {entry.evidence && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-control,8px)] bg-background/70 p-2 font-mono text-xs leading-4 text-muted-foreground">{entry.evidence}</pre>}
              <p className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                {typeof entry.confidence === "number" && <span>confidence {Math.round(entry.confidence * 100)}%</span>}
                {entry.verified && <span className="text-emerald-400">verified</span>}
              </p>
            </li>)}
          </ul>
        </div>)}
        <Button variant="outline" size="sm" className="w-full" onClick={() => focus(selected.id)}>Center on map</Button>
      </div>
    </aside>}

    {graph.isPending && <div className="absolute inset-0 grid place-items-center"><LoadingState label="Mapping memory…" /></div>}
    {graph.data?.missing && <div className="absolute inset-0 grid place-items-center"><div className="text-center"><p className="text-lg font-medium">No memory yet</p><p className="mt-2 text-sm text-muted-foreground">Run an engagement — everything the librarian learns lands here.</p></div></div>}
    {graph.error && <div className="absolute inset-0 grid place-items-center"><p role="alert" className="text-sm text-destructive">Could not load the memory map.</p></div>}
  </div>;
}
