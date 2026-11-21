import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import cytoscape from "cytoscape";
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

const LEGEND: { color: string; label: string }[] = [
  { color: "#14b8a6", label: "External targets" },
  { color: "#fb923c", label: "Internal targets" },
  { color: "#60a5fa", label: "Info" },
  { color: "#2dd4bf", label: "Endpoints" },
  { color: "#ef4444", label: "Vulnerabilities" },
  { color: "#94a3b8", label: "Unsorted" },
];

/** Daisy mindmap (MindMeister style): every expanded node places its
 * children on a staggered circle AROUND itself — the map grows outward from
 * wherever you are, never re-flowing the whole world. Hue flows down each
 * branch. Positions exist for every node; visibility is applied separately. */
function mindmapLayout(nodes: GraphNode[], edges: GraphEdge[], collapsed: Set<string>): { pos: Record<string, { x: number; y: number }>; hue: Record<string, string>; parentOf: Record<string, string>; children: Record<string, string[]> } {
  const adj: Record<string, string[]> = {};
  for (const e of edges) {
    // cross-links (evidence mentions between targets) must not shape layout
    if (e.ekind === "cross") continue;
    (adj[e.source] ||= []).push(e.target);
    (adj[e.target] ||= []).push(e.source);
  }
  const fullChildren: Record<string, string[]> = {};
  const parentOf: Record<string, string> = {};
  const depthOf: Record<string, number> = { __root__: 0 };
  const fullQueue = ["__root__"];
  for (let qi = 0; qi < fullQueue.length; qi++) {
    const id = fullQueue[qi]!;
    const d = depthOf[id]!;
    const kids = (adj[id] ?? []).filter(k => depthOf[k] === undefined);
    fullChildren[id] = kids;
    for (const k of kids) { depthOf[k] = d + 1; parentOf[k] = id; fullQueue.push(k); }
  }
  const hue: Record<string, string> = {
    __root__: "#64748b", __ext__: "#14b8a6", __int__: "#fb923c", __unsorted__: "#94a3b8",
  };
  for (let qi = 0; qi < fullQueue.length; qi++) {
    const id = fullQueue[qi]!;
    for (const k of fullChildren[id] ?? []) hue[k] = hue[k] ?? hue[id] ?? "#94a3b8";
  }
  // hidden = descendants of collapsed pills — they do not consume layout
  // space until their branch is expanded (this is the anti-pile fix).
  const hidden = new Set<string>();
  for (const id of fullQueue) {
    if (!collapsed.has(id)) continue;
    const stack = [...(fullChildren[id] ?? [])];
    while (stack.length) {
      const h = stack.pop()!;
      if (hidden.has(h)) continue;
      hidden.add(h);
      for (const k of fullChildren[h] ?? []) stack.push(k);
    }
  }
  // separate visited map — depthOf was already filled by the full-tree BFS
  // above, and reusing it here filtered out every node (all targets fell to
  // fallback positions = the overlap pile).
  const children: Record<string, string[]> = {};
  const visDepth: Record<string, number> = { __root__: 0 };
  const queue = ["__root__"];
  for (let qi = 0; qi < queue.length; qi++) {
    const id = queue[qi]!;
    const d = visDepth[id]!;
    const kids = (adj[id] ?? []).filter(k => visDepth[k] === undefined && !hidden.has(k));
    children[id] = kids;
    for (const k of kids) { visDepth[k] = d + 1; queue.push(k); }
  }
  const leaves: Record<string, number> = {};
  const countLeaves = (id: string): number => {
    if (leaves[id] !== undefined) return leaves[id];
    const kids = children[id] ?? [];
    const v = kids.length ? kids.reduce((s, k) => s + countLeaves(k), 0) : 1;
    leaves[id] = v;
    return v;
  };
  countLeaves("__root__");
  const pos: Record<string, { x: number; y: number }> = { __root__: { x: 0, y: 0 } };
  const LEVEL = 240;
  const LEAF = 30;
  const COL_MAX = 22;
  const COL_PITCH = 300;
  const stackVertical = (ids: string[], x: number, yTop: number, side: 1 | -1, depth: number): { bottom: number; center: number } => {
    let y = yTop;
    let first = 0, last = 0, had = false;
    for (const k of ids) {
      const r = placeSubtree(k, x, y, side, depth);
      if (!had) { first = r.center; had = true; }
      last = r.center;
      y = r.bottom;
    }
    return { bottom: y, center: had ? (first + last) / 2 : yTop + LEAF / 2 };
  };
  function placeSubtree(id: string, x: number, yTop: number, side: 1 | -1, depth: number): { bottom: number; center: number } {
    const kids = children[id] ?? [];
    if (!kids.length) {
      const yc = yTop + LEAF / 2;
      pos[id] = { x, y: yc };
      return { bottom: yTop + LEAF, center: yc };
    }
    if (kids.length > COL_MAX) {
      const cols = Math.ceil(kids.length / COL_MAX);
      const chunkSize = Math.ceil(kids.length / cols);
      let xCol = x + side * COL_PITCH;
      let maxBottom = yTop;
      const centers: number[] = [];
      for (let ci = 0; ci < cols; ci++) {
        const chunk = kids.slice(ci * chunkSize, (ci + 1) * chunkSize);
        const r = stackVertical(chunk, xCol, yTop, side, depth + 1);
        centers.push(r.center);
        maxBottom = Math.max(maxBottom, r.bottom);
        xCol += side * COL_PITCH;
      }
      const yc = (Math.min(...centers) + Math.max(...centers)) / 2;
      pos[id] = { x, y: yc };
      return { bottom: Math.max(maxBottom, yTop + LEAF), center: yc };
    }
    const r = stackVertical(kids, x + side * LEVEL, yTop, side, depth + 1);
    const yc = r.center;
    pos[id] = { x, y: yc };
    return { bottom: Math.max(r.bottom, yTop + LEAF), center: yc };
  }
  const colHeight = (n: number) => { const c = Math.max(1, Math.ceil(n / COL_MAX)); return Math.ceil(n / c) * LEAF; };
  const H = Math.max(colHeight(leaves["__ext__"] ?? 1), colHeight(leaves["__int__"] ?? 1), 500);
  placeSubtree("__ext__", -LEVEL, -H / 2, -1, 1);
  placeSubtree("__int__", LEVEL, -H / 2, 1, 1);
  placeSubtree("__unsorted__", -LEVEL * 2.2, H / 2 + 80, -1, 1);
  nodes.forEach((n, i) => {
    if (pos[n.id]) return;
    const p = parentOf[n.id];
    const base = pos[p] ?? { x: 0, y: 0 };
    const a = (i * 2.39996323) % (Math.PI * 2);
    pos[n.id] = { x: base.x + 110 + Math.cos(a) * 80, y: base.y + Math.sin(a) * 80 };
  });
  return { pos, hue, parentOf, children: fullChildren };
}

function applyCollapse(cy: any, parentOf: Record<string, string>, collapsed: Set<string>) {
  cy.batch(() => {
    cy.nodes().forEach((n: any) => {
      let hidden = false;
      let cur: string | undefined = n.id();
      while (cur && cur !== "__root__") {
        if (collapsed.has(cur)) { hidden = true; break; }
        cur = parentOf[cur];
      }
      n.style("display", hidden ? "none" : "element");
    });
    cy.edges().forEach((e: any) => e.style("display",
      e.source().style("display") === "none" || e.target().style("display") === "none" ? "none" : "element"));
  });
}

export function MemoryMindmapPage() {
  const { webFetchJson } = useBackendApi();
  const navigate = useNavigate();
  const canvasRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<any>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const graph = useQuery({ queryKey: ["memory-graph"], queryFn: ({ signal }) => webFetchJson<GraphPayload>("/api/console/memory-graph", { signal }), refetchOnWindowFocus: false });

  // Branch-level collapse: a branch pill shows; its leaves reveal on click.
  // Targets/branches are always placed (positions exist for every node).
  const [collapsedNodes, setCollapsedNodes] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const layout = useMemo(() => (graph.data ? mindmapLayout(graph.data.nodes, graph.data.edges, collapsedNodes) : { pos: {} as Record<string, { x: number; y: number }>, hue: {} as Record<string, string>, parentOf: {} as Record<string, string>, children: {} as Record<string, string[]> }), [graph.data, collapsedNodes]);
  const selected = useMemo(() => graph.data?.nodes.find(n => n.id === selectedId) ?? null, [graph.data, selectedId]);

  useEffect(() => {
    if (!graph.data || !canvasRef.current) return;
    const container = canvasRef.current;
    // Inline geometry — Tailwind inset-0 collapsed to 0px height at init in
    // the field, leaving every cytoscape canvas 1600x0 (blank map).
    container.style.position = "absolute";
    container.style.inset = "0";
    container.style.width = "100%";
    container.style.height = "100%";
    const elements = [
      ...graph.data.nodes.map(n => {
        const hue = layout.hue[n.id] ?? "#94a3b8";
        // Structural pills (hubs + the 3 branch pills) = solid hue, dark text —
        // the colorful skeleton. Content pills (targets, endpoints, leaves) =
        // dark fill + colored border + light text, so 60 targets stay distinct.
        const isHub = n.kind === "hub"; const isStruct = isHub || n.kind.startsWith("branch:");
        const isBranchPill = n.kind.startsWith("branch:");
        const sev = n.kind === "vulns:leaf" && n.color ? n.color : "";
        const fill = isStruct ? hue : (sev || "#0f1317");
        const border = isStruct ? "#0b0e11" : (sev || hue);
        const textColor = isStruct ? "#04211f" : "#dbe4ee";
        const h = n.kind === "hub" ? 44 : n.kind.startsWith("target") ? 32 : n.kind.startsWith("branch:") ? 28 : 24;
        return {
          data: {
            id: n.id, label: n.kind.startsWith("target") ? `${n.label} · ${n.entryCount}` : n.label,
            color: border, fill, h, border2: hue, text: textColor,
            bw: isBranchPill || isHub ? 2 : 1.5,
            raw: n,
            imp: (n.kind.startsWith("target") || n.kind === "hub" || n.entryCount >= 8) ? 1 : 0,
            branchnode: n.kind.startsWith("branch") ? 1 : 0,
          },
          classes: n.kind === "hub" ? "hubnode" : "",
          position: layout.pos[n.id] ?? { x: 0, y: 0 },
        };
      }),
      ...graph.data.edges.map(e => {
        const src = e.source, dst = e.target;
        const w = src === "__root__" ? 5 : src === "__ext__" || src === "__int__" ? 3.5 : 2;
        // tint the connector with the CHILD branch hue, never the root gray
        const ec = layout.hue[src === "__root__" ? dst : src] ?? "#64748b";
        return { data: { source: src, target: dst, color: ec, w } };
      }),
    ];
    const cy = cytoscape({
      container,
      elements,
      layout: { name: "preset", fit: false },
      wheelSensitivity: 0.25,
      minZoom: 0.3, maxZoom: 2.5,
      style: [
        // MindMeister-style pills: rounded rect, width auto-fits the label,
        // tinted fill of the branch hue, colored border, text centered.
        { selector: "node", style: {
          shape: "round-rectangle",
          width: "label", height: "data(h)",
          "background-color": "data(fill)", "background-opacity": 1,
          "border-width": "data(bw)", "border-color": "data(color)",
          label: "data(label)",
          color: "data(text)", "font-size": 11, "font-family": "system-ui",
          "font-weight": 600,
          "text-valign": "center", "text-halign": "center",
          "text-wrap": "ellipsis", "text-max-width": 160,
          "min-zoomed-font-size": 6,
        } },
        { selector: "node[imp >= 1]", style: {
          "font-size": 12, "min-zoomed-font-size": 4,
        } },
        { selector: "node[branchnode]", style: {
          "font-size": 10, "color": "#8b949e", "min-zoomed-font-size": 6,
          "text-opacity": 0.7,
        } },
        { selector: "node.hubnode", style: {
          "font-size": 15, "font-weight": 700, "min-zoomed-font-size": 3,
          "color": "#f8fafc",
        } },
        { selector: "edge", style: {
          width: "data(w)", "line-color": "data(color)", "line-opacity": 0.9,
          "curve-style": "bezier", "control-point-step-size": 70,
        } },
        { selector: "node.dim, edge.dim", style: { opacity: 0.12 } },
        { selector: "node:selected", style: { "border-width": 2.5, "border-color": "#f8fafc" } },
      ],
    });
    cyRef.current = cy;
    // Exposed for headless verification (operator screenshot rule)
    (window as any).__memoryCy = cy;
    // Explicit resize + fit after init — the canvases were 0px tall when the
    // container measured before layout; resize() re-reads the real size.
    cy.resize();
    applyCollapse(cy, layout.parentOf, collapsedNodes);
    cy.fit(cy.elements().filter((el: any) => el.style("display") !== "none"), 60);
    cy.on("tap", "node", (evt: any) => {
      const id = String(evt.target.id());
      const raw = evt.target.data("raw");
      const kind = String(raw?.kind ?? "");
      if (kind === "branch") {
        // hub pill: frame its visible sub-tree (drill-in view)
        requestAnimationFrame(() => { try {
          const c = cyRef.current;
          const b = c.getElementById(id);
          c.animate({ center: { eles: b }, zoom: Math.max(c.zoom(), 0.7) }, { duration: 250 });
        } catch {} });
        setSelectedId(id);
      } else if (kind.startsWith("branch:")) {
        // branch pill: reveal / hide its leaves, then frame the branch
        setCollapsedNodes(prev => { const nx = new Set(prev); if (nx.has(id)) nx.delete(id); else nx.add(id); return nx; });
        setSelectedId(null);
        requestAnimationFrame(() => { try {
          const c = cyRef.current;
          const b = c.getElementById(id);
          c.animate({ center: { eles: b }, zoom: Math.max(c.zoom(), 0.75) }, { duration: 250 });
        } catch {} });
      } else if (kind.startsWith("target")) {
        // target pill: click reveals its Info/Endpoints/Vulnerabilities branch
        // pills; click again folds them back. Inspector shows the rollup.
        // read CURRENT state inside the updater — a stale closure collapsed
        // branches instead of expanding them
        setCollapsedNodes(prev => {
          const branchIds = (layout.children[id] ?? []).filter((cid: string) => cid.includes("/info") || cid.includes("/endpoints") || cid.includes("/vulns"));
          const anyVisible = branchIds.some(bid => !prev.has(bid));
          const nx = new Set(prev);
          for (const bid of branchIds) { if (anyVisible) nx.add(bid); else nx.delete(bid); }
          return nx;
        });
        setSelectedId(id);
        requestAnimationFrame(() => { try {
          const c = cyRef.current;
          const b = c.getElementById(id);
          c.animate({ center: { eles: b }, zoom: Math.max(c.zoom(), 0.7) }, { duration: 250 });
        } catch {} });
      } else {
        setSelectedId(id);
      }
    });
    cy.on("tap", (e: any) => { if (e.target === cy) setSelectedId(null); });
    cy.on("mouseover", "node", (evt: any) => {
      cy.elements().addClass("dim");
      evt.target.connectedEdges().connectedNodes().union(evt.target).removeClass("dim");
    });
    cy.on("mouseout", "node", () => cy.elements().removeClass("dim"));
    return () => { cy.destroy(); cyRef.current = null; };
  }, [graph.data, layout]);

  // Re-apply collapse + re-fit whenever the collapsed set flips (expand or
  // collapse of any pill) so the viewport re-frames the visible tree.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !graph.data) return;
    applyCollapse(cy, layout.parentOf, collapsedNodes);
    cy.fit(cy.nodes().filter((n: any) => n.style("display") !== "none"), 60);
  }, [graph.data, layout, collapsedNodes]);

  // Seed: every Info/Endpoints/Vulnerabilities branch starts collapsed — its
  // pill shows under the target; clicking the branch pill reveals its leaves.
  useEffect(() => {
    if (!graph.data) return;
    setCollapsedNodes(prev => {
      if (prev.size > 0) return prev;
      const s = new Set(prev);
      for (const n of graph.data.nodes) if (n.kind.startsWith("branch:")) s.add(n.id);
      return s;
    });
  }, [graph.data]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { if (selectedId) setSelectedId(null); else navigate("/chats"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, navigate]);

  const focusNode = (id: string) => {
    const cy = cyRef.current;
    if (!cy) return;
    const node = cy.getElementById(id);
    if (node.length) { cy.animate({ center: { eles: node }, zoom: 1.4 }, { duration: 300 }); setSelectedId(id); }
  };
  const matches = query.trim() && graph.data
    ? graph.data.nodes.filter(n => !n.id.startsWith("__") && n.label.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 8)
    : [];
  const stats = graph.data?.stats;
  const extCount = graph.data?.nodes.filter(n => n.kind === "target:external").length ?? 0;
  const intCount = graph.data?.nodes.filter(n => n.kind === "target:internal").length ?? 0;

  return <div className="fixed inset-0 z-[100] bg-[#0b0d10] text-foreground">
    <div ref={canvasRef} className="absolute inset-0" />

    {/* back + controls */}
    <div className="absolute left-4 top-4 z-10 flex flex-col gap-2">
      <Button variant="outline" size="sm" onClick={() => navigate("/chats")} className="bg-[#111418]/90"><ArrowLeft className="size-4" />Back</Button>
      <div className="flex gap-1.5">
        <Button variant="outline" size="icon-sm" title="Fit graph" aria-label="Fit graph" className="bg-[#111418]/90" onClick={() => cyRef.current?.fit(undefined, 60)}><Crosshair className="size-4" /></Button>
        <Button variant="outline" size="icon-sm" title="Reload memory" aria-label="Reload memory" className="bg-[#111418]/90" disabled={graph.isFetching} onClick={() => void graph.refetch()}><RefreshCcw className="size-4" /></Button>
      </div>
      <div className="relative w-64">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search targets, hosts, agents…" aria-label="Search the memory map" className="h-9 bg-[#111418]/90 pl-9 text-sm" autoComplete="off" />
        {matches.length > 0 && <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-72 overflow-y-auto rounded-[var(--radius-menu,12px)] border border-border bg-[#111418] p-1 shadow-xl">
          {matches.map(m => <button key={m.id} type="button" onClick={() => { focusNode(m.id); setQuery(""); }}
            className="flex w-full items-center gap-2 rounded-[var(--radius-control,8px)] px-2.5 py-1.5 text-left text-sm hover:bg-muted/60">
            <span className="size-2 shrink-0 rounded-full" style={{ background: layout.hue[m.id] ?? m.color ?? "#94a3b8" }} />
            <span className="min-w-0 flex-1 truncate">{m.label}</span>
            <span className="shrink-0 text-xs text-muted-foreground">{m.kind.startsWith("target") ? "Target" : m.kind.startsWith("branch") ? "Branch" : m.kind.replace(":leaf", "")}</span>
          </button>)}
        </div>}
      </div>
    </div>

    {/* stats + legend */}
    {stats && <div className="absolute bottom-4 left-4 z-10 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="rounded-full bg-[#111418]/90 px-2.5 py-1 font-mono">{stats.nodes} nodes</span>
        <span className="rounded-full bg-[#111418]/90 px-2.5 py-1 font-mono">{stats.edges} links</span>
        <span className="rounded-full bg-[#111418]/90 px-2.5 py-1 font-mono">{stats.entries} intel entries</span>
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
        <span className="mt-1 size-3 shrink-0 rounded-full" style={{ background: layout.hue[selected.id] ?? selected.color ?? "#94a3b8" }} />
        <div className="min-w-0 flex-1"><h2 className="break-all text-sm font-semibold" title={selected.id}>{selected.label}</h2>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{selected.kind.replace(":leaf", " entry").replace(":", " · ")}</span>
            {selected.internal && <span className="rounded-full bg-orange-500/15 px-2 py-0.5 text-orange-300">internal</span>}
            {selected.severity && <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-red-300">{selected.severity}</span>}
            <span>{selected.entryCount} intel entries</span>
          </p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close inspector" onClick={() => setSelectedId(null)}><X className="size-4" /></Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {selected.entries.length === 0 && <p className="text-sm text-muted-foreground">No intel entries recorded for this node.</p>}
        {Object.entries(selected.categories ?? {}).map(([category, count]) => <div key={category} className="mb-5">
          <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{category} <span className="font-mono">{count}</span></h3>
          <ul className="space-y-3">
            {selected.entries.filter(entry => entry.category === category).map((entry, i) => <li key={i} className="rounded-[var(--radius-menu,12px)] border border-border/50 bg-muted/20 p-3">
              <p className="text-sm leading-5">{entry.rule}</p>
              {entry.evidence && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-control,8px)] bg-background/70 p-2 font-mono text-xs leading-4 text-muted-foreground">{entry.evidence}</pre>}
              <p className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                {typeof entry.confidence === "number" && <span>confidence {Math.round(entry.confidence * 100)}%</span>}
                {entry.verified && <span className="text-emerald-400">verified</span>}
              </p>
            </li>)}
          </ul>
        </div>)}
        <Button variant="outline" size="sm" className="w-full" onClick={() => { focusNode(selected.id); }}>Center on map</Button>
      </div>
    </aside>}

    {graph.isPending && <div className="absolute inset-0 grid place-items-center"><LoadingState label="Mapping memory…" /></div>}
    {graph.data?.missing && <div className="absolute inset-0 grid place-items-center"><div className="text-center"><p className="text-lg font-medium">No memory yet</p><p className="mt-2 text-sm text-muted-foreground">Run an engagement — everything the librarian learns lands here.</p></div></div>}
    {graph.error && <div className="absolute inset-0 grid place-items-center"><p role="alert" className="text-sm text-destructive">Could not load the memory map.</p></div>}
  </div>;
}
