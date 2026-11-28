import { Braces, Radio, Waypoints } from "lucide-react";
import { PageContent, PageHeader } from "@/components/page-header";
import { DataBrowser } from "@/ops/data-browser";
import { Mono, Panel } from "@/ops/ui";

const DOORS = [
  { icon: Braces, name: "Python", body: <>Import <Mono>data_hub</Mono> from <Mono>suijin.server.web</Mono>: <Mono>catalog()</Mono>, <Mono>read(store, **filters)</Mono>, <Mono>watch(fn)</Mono>.</> },
  { icon: Waypoints, name: "HTTP", body: <><Mono>GET /api/data/catalog</Mono>, <Mono>GET /api/data/stores/{"{id}"}</Mono>, and <Mono>WS /api/data/stream</Mono> for every change.</> },
  { icon: Radio, name: "Mesh bus", body: <>Ask on <Mono>/data/query</Mono>; every change is published on <Mono>/data/change</Mono> as <Mono>{"{store, rows, at}"}</Mono>.</> },
];

/** Every piece of local Suijin data, in one browsable place. */
export function DataPage() {
  return (
    <PageContent>
      <PageHeader
        title="Data"
        summary="Every place Suijin keeps data: the swarm database, each engagement, the config, the knowledge graph, the registries. With live row counts, and the same door open to plugins and scripts."
      />
      <DataBrowser />
      <Panel>
        <div className="grid gap-6 md:grid-cols-3">
          {DOORS.map((door) => (
            <div key={door.name} className="flex items-start gap-3 text-sm leading-6 text-muted-foreground">
              <door.icon className="mt-1 size-4 shrink-0" aria-hidden="true" />
              <div><div className="text-foreground">{door.name}</div>{door.body}</div>
            </div>
          ))}
        </div>
      </Panel>
    </PageContent>
  );
}
