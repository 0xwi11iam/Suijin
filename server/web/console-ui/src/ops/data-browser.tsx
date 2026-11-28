/**
 * The data hub, browsable: every place Suijin keeps data, grouped, with its live rows — and the
 * Python door printed beside them for plugins and scripts.
 */
import { useMemo, useState, type ReactNode } from "react";
import { Braces, ChevronLeft, ChevronRight, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Tooltip } from "@/components/ui/tooltip";
import { Table, TableBody, TableHeader, TableRow } from "@/components/ui/table";
import { JsonScalar } from "@/console/json-view";
import { useCatalog, useStoreKinds, useStoreRows, type Catalog, type StoreInfo } from "./api";
import { ErrorNote, JsonDialog, Mono, StateBadge, Td, Th } from "./ui";

const SNIPPET = `from suijin.server.web import data_hub

stores = data_hub.catalog()                       # every store, with live row counts
agents = data_hub.read("swarm.agents")["rows"]    # rows, as plain dicts
open_work = data_hub.read("swarm.intents", state="open", limit=20)
events = data_hub.read("engagement.<slug>.events", kind="tool.call", limit=50)

def changed(store_id, payload):
    print(store_id, "now has", payload.get("rows"), "rows")

unwatch = data_hub.watch(changed)                 # also published on the mesh bus as /data/change`;

const SENSITIVITY = {
  public: { label: "public", tone: "neutral" as const, hint: "shipped with Suijin or harmless on its own" },
  operator: { label: "operator", tone: "sky" as const, hint: "your own state; secrets are masked before anything reaches this page" },
  secret: { label: "secret", tone: "amber" as const, hint: "credentials — only names and counts ever leave the process" },
};

const MAX_COLUMNS = 7;

const storeButton = (store: StoreInfo, selected: string | null, onSelect: (id: string) => void, label: ReactNode, key = store.id) => (
  <button
    key={key}
    type="button"
    onClick={() => onSelect(store.id)}
    aria-current={selected === store.id}
    data-store-id={store.id}
    className={`flex w-full items-baseline gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-[var(--surface-hover)] ${selected === store.id ? "bg-[var(--surface-selected)] text-foreground" : "text-muted-foreground"}`}
  >
    <span className="min-w-0 flex-1 truncate" title={store.id}>{label}</span>
    <span className="shrink-0 text-[11px] tabular-nums opacity-70">{store.rows ?? "—"}</span>
  </button>
);

/** Engagement stores sit under their engagement, named `events`, `files`, `exploits`. */
function EngagementStores({ catalog, selected, onSelect }: { catalog: Catalog; selected: string | null; onSelect: (id: string) => void }) {
  const stores = catalog.stores.filter((store) => store.group === "engagements");
  const bySlug = new Map<string, { name: string; store: StoreInfo }[]>();
  const singles: StoreInfo[] = [];
  for (const store of stores) {
    const match = /^engagement\.(.+)\.(events|files|exploits)$/.exec(store.id);
    if (!match) {
      singles.push(store);
      continue;
    }
    const list = bySlug.get(match[1]) ?? [];
    list.push({ name: match[2], store });
    bySlug.set(match[1], list);
  }
  const short = (slug: string) => (slug.length > 30 ? `${slug.slice(0, 18)}…${slug.slice(-9)}` : slug);
  return (
    <div data-store-group="engagements">
      <div className="sticky top-0 z-10 bg-card px-3 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        engagements <span className="font-normal tabular-nums">· {stores.length}</span>
      </div>
      {singles.map((store) => storeButton(store, selected, onSelect, store.label))}
      {[...bySlug].map(([slug, items]) => (
        <div key={slug} data-engagement-stores={slug}>
          <div className="truncate px-3 pt-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70" title={slug}>{short(slug)}</div>
          {items.map(({ name, store }) => storeButton(store, selected, onSelect, <span className="pl-3">{name}</span>, store.id))}
        </div>
      ))}
    </div>
  );
}

function StoreList({ catalog, selected, onSelect }: { catalog: Catalog; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <nav aria-label="Data stores" data-stores="" className="max-h-[42rem] overflow-auto">
      {catalog.groups.map((group) =>
        group.id === "engagements" ? (
          <EngagementStores key={group.id} catalog={catalog} selected={selected} onSelect={onSelect} />
        ) : (
          <div key={group.id} data-store-group={group.id}>
            <div className="sticky top-0 z-10 bg-card px-3 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {group.id} <span className="font-normal tabular-nums">· {group.stores}</span>
            </div>
            {catalog.stores.filter((store) => store.group === group.id).map((store) => storeButton(store, selected, onSelect, store.label))}
          </div>
        ),
      )}
    </nav>
  );
}

function StoreTable({ store }: { store: StoreInfo }) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<Record<string, unknown> | null>(null);
  const rows = useStoreRows(store.id, q, kind || undefined, offset);
  const total = rows.data?.total ?? null;
  const data = rows.data?.rows ?? [];
  const columns = useMemo(() => {
    const keys = new Set<string>();
    for (const row of data.slice(0, 50)) for (const key of Object.keys(row)) keys.add(key);
    const preferred = store.columns.filter((column) => keys.has(column));
    const rest = [...keys].filter((key) => !preferred.includes(key));
    return [...preferred, ...rest].slice(0, MAX_COLUMNS);
  }, [data, store.columns]);
  const isJournal = store.kind === "jsonl";
  const sensitivity = SENSITIVITY[store.sensitivity] ?? SENSITIVITY.operator;
  const [python, setPython] = useState(false);

  const reset = () => setOffset(0);

  return (
    <div className="flex min-w-0 flex-col">
      <header className="space-y-1 border-b border-border/60 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-medium text-foreground">{store.label}</h2>
          <Badge variant="neutral" className="font-mono text-[11px] font-normal">{store.kind}</Badge>
          <StateBadge tone={sensitivity.tone}>{sensitivity.label}</StateBadge>
        </div>
        <Mono className="block break-all text-[11px] text-muted-foreground">{store.where}</Mono>
        <p className="text-xs leading-5 text-muted-foreground">{store.description} {sensitivity.hint}.</p>
      </header>
      <div className="flex flex-wrap items-center gap-2 border-b border-border/60 px-4 py-2">
        <div className="relative min-w-[12rem] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input value={q} onChange={(event) => { setQ(event.target.value); reset(); }} placeholder="Filter every column" aria-label="Filter rows" className="pl-8" />
        </div>
        {isJournal && <JournalKinds storeId={store.id} value={kind} onChange={(next) => { setKind(next); reset(); }} />}
        <Tooltip content="Read this store from Python"><Button type="button" size="sm" variant="secondary" onClick={() => setPython(true)}><Braces className="size-3.5" />Python</Button></Tooltip>
      </div>
      {rows.error && <div className="px-4 pt-3"><ErrorNote error={rows.error} /></div>}
      {rows.data?.error && <div className="px-4 pt-3"><ErrorNote error={rows.data.error} /></div>}
      <div className="min-h-[16rem] overflow-auto">
        <Table>
          <TableHeader>
            <TableRow>{columns.map((column) => <Th key={column} className="font-mono normal-case tracking-normal">{column}</Th>)}</TableRow>
          </TableHeader>
          <TableBody>
            {!data.length && <tr><td colSpan={Math.max(1, columns.length)} className="px-4 py-10 text-center text-sm text-muted-foreground">{rows.isPending ? "Reading…" : "No rows."}</td></tr>}
            {data.map((row, index) => (
              <TableRow key={String(row.id ?? row.key ?? row.name ?? row.seq ?? index)} className="cursor-pointer" onClick={() => setOpen(row)}>
                {columns.map((column) => (
                  <Td key={column} className="max-w-[18rem]"><div className="truncate" title={JSON.stringify(row[column] ?? null)}><JsonScalar value={row[column]} /></div></Td>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <footer className="flex items-center justify-between gap-3 border-t border-border/60 px-4 py-2 text-xs text-muted-foreground">
        <span className="tabular-nums">{data.length ? `${offset + 1}–${offset + data.length}` : "0"}{total != null ? ` of ${total}` : ""}</span>
        <div className="flex items-center gap-1">
          <Button type="button" variant="ghost" size="icon-xs" aria-label="Newer rows" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}><ChevronLeft className="size-3.5" /></Button>
          <Button type="button" variant="ghost" size="icon-xs" aria-label="Older rows" disabled={total != null ? offset + 100 >= total : data.length < 100} onClick={() => setOffset(offset + 100)}><ChevronRight className="size-3.5" /></Button>
        </div>
      </footer>
      <JsonDialog open={Boolean(open)} onOpenChange={(next) => !next && setOpen(null)} title={`${store.id} — row`} value={open} />
      <JsonDialog open={python} onOpenChange={setPython} title={`Read ${store.id} from Python`} description="Plugins and scripts use the same door this page does." text={SNIPPET} />
    </div>
  );
}

/** The kinds of a journal, with their counts, for the filter. */
function JournalKinds({ storeId, value, onChange }: { storeId: string; value: string; onChange: (kind: string) => void }) {
  const kinds = useStoreKinds(storeId).data?.kinds ?? [];
  if (!kinds.length) return null;
  return (
    <Select value={value || "all"} onValueChange={(next) => onChange(next === "all" ? "" : next)} aria-label="Record kind" className="w-auto min-w-[10rem]"
      options={[{ value: "all", label: "every kind" }, ...kinds.map((entry) => ({ value: entry.kind, label: `${entry.kind} (${entry.rows})` }))]} />
  );
}

export function DataBrowser() {
  const catalog = useCatalog();
  const [selected, setSelected] = useState<string | null>(null);
  const data = catalog.data;
  const store = data?.stores.find((entry) => entry.id === selected) ?? null;
  if (catalog.error && !data) return <ErrorNote error={catalog.error} />;
  if (!data) return <div className="py-10 text-center text-sm text-muted-foreground">Reading the catalog…</div>;
  const first = store?.id ?? data.stores[0]?.id ?? null;
  return (
    <div className="grid min-h-[28rem] overflow-hidden rounded-[var(--radius-panel)] border border-border/60 bg-card xl:grid-cols-[18rem_1fr]">
      <div className="border-b border-border/60 xl:border-b-0 xl:border-r">
        <StoreList catalog={data} selected={first} onSelect={setSelected} />
      </div>
      {first ? <StoreTable key={first} store={data.stores.find((entry) => entry.id === first)!} /> : <div className="p-8 text-sm text-muted-foreground">No store is registered.</div>}
    </div>
  );
}
