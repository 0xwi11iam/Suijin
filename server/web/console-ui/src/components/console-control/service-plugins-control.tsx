import { LoadingState } from "@/components/state-panel";
import { useRef, useState } from "react";
import type { ServicePluginItem, ServicePluginsResponse } from "@0/engine/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plug } from "lucide-react";
import { useBackendApi } from "@/api";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Feedback } from "./control-ui";

function ServiceIcon({ item }: { item: ServicePluginItem & { icon?: string } }) {
  const [failed, setFailed] = useState(false);
  if (item.icon && !failed) return <img src={item.icon} alt="" loading="lazy" className="size-6 rounded-[4px] object-contain" onError={() => setFailed(true)} />;
  const initial = (item.name || item.id || "?").charAt(0).toUpperCase();
  return <span aria-hidden="true" className="inline-flex size-6 items-center justify-center rounded-[4px] bg-muted text-xs font-bold">{failed ? initial : <Plug aria-hidden="true" className="size-4" />}</span>;
}

export function ServicePluginsControl({ filter = "" }: { filter?: string }) {
  const { webFetchJson } = useBackendApi();
  const queryClient = useQueryClient();
  const inventory = useQuery({ queryKey: ["console-service-plugins"], queryFn: ({ signal }) => webFetchJson<ServicePluginsResponse>("/api/console/service-plugins", { signal }), refetchInterval: 15_000 });
  const [tab, setTab] = useState<"all" | "connected">("all");
  const trigger = useRef<HTMLElement | null>(null);
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["console-service-plugins"] }),
      queryClient.invalidateQueries({ queryKey: ["console-tools"] }),
      queryClient.invalidateQueries({ queryKey: ["workflow-tool-catalog"] }),
    ]);
  };
  // The switch is REAL: PATCH /api/console/service-plugins/{id} flips the
  // module's enabled state (core tier refuses on the server).
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => webFetchJson<{ ok?: boolean; message?: string }>(`/api/console/service-plugins/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
    onSuccess: async result => {
      await refresh();
    },
    onError: () => { void inventory.refetch(); },
  });
  const connectedCount = inventory.data?.items.filter(item => item.configured).length ?? 0;
  const items = inventory.data?.items.filter(item => (tab === "all" || item.configured) && `${item.name} ${item.description} ${item.category}`.toLowerCase().includes(filter.toLowerCase())) ?? [];
  return <section className="space-y-5" aria-label="Service plugins">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-base font-medium">Integrations</h2>
      <div className="flex gap-1 rounded-full bg-muted/50 p-1" role="group" aria-label="Filter service plugins">
        <Button variant={tab === "all" ? "secondary" : "ghost"} size="sm" aria-pressed={tab === "all"} onClick={() => setTab("all")}>All</Button>
        <Button variant={tab === "connected" ? "secondary" : "ghost"} size="sm" aria-pressed={tab === "connected"} onClick={() => setTab("connected")}>Active{connectedCount > 0 ? ` · ${connectedCount}` : ""}</Button>
      </div>
    </div>
    <p className="text-xs text-muted-foreground">Every installed Suijin module pack. The switch enables or disables the module; core-tier packs cannot be disabled.</p>
    {inventory.isPending && <LoadingState label="Loading integrations…" variant="cards" />}
    {inventory.error && <div className="space-y-3"><Feedback error={inventory.error} /><Button variant="secondary" onClick={() => void inventory.refetch()} disabled={inventory.isFetching}>Try again</Button></div>}
    {inventory.data && items.length === 0 && <p className="py-8 text-sm text-muted-foreground">{filter.trim() ? "No integrations match your search." : "No modules available."}</p>}
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {items.map(item => <article key={item.id} className="flex min-w-0 flex-col gap-4 rounded-[var(--radius-panel,16px)] bg-muted/35 p-5 transition-colors duration-150 motion-reduce:transition-none hover:bg-muted/55" ref={element => { if (toggle.variables?.id === item.id) trigger.current = element; }}>
        <div className="flex items-center gap-3"><div className="flex size-10 shrink-0 items-center justify-center rounded-[var(--radius-menu,12px)] bg-background/60 text-foreground"><ServiceIcon item={item} /></div><h3 className="min-w-0 flex-1 text-sm font-medium">{item.name}</h3>{item.configured && <Switch checked={item.enabled} aria-label={`Enable ${item.name}`} disabled={toggle.isPending && toggle.variables?.id === item.id} onCheckedChange={enabled => toggle.mutate({ id: item.id, enabled })} />}</div>
        <p className="flex-1 text-sm leading-6 text-muted-foreground">{item.description || "Suijin module pack."}</p>
        <div className="flex items-center justify-between gap-3">
          <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">{item.enabled && <Check aria-hidden="true" className="size-3.5 text-primary-text" />}{item.enabled ? "Active" : "Off"}</span>
        </div>
        {toggle.variables?.id === item.id && toggle.error && <Feedback error={toggle.error} />}
      </article>)}
    </div>
  </section>;
}
