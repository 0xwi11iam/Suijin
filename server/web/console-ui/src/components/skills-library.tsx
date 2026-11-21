import { useState } from "react";
import { ArrowUpRight, BookOpen, Bug, Cloud, FileText, KeyRound, Plus, Radar, Search, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { LoadingState } from "@/components/state-panel";

export type SkillLibraryItem = {
  id: string;
  name: string;
  description: string;
  scope: string;
  revision: string;
  writable: boolean;
  fileCount: number;
  source: string;
  category?: string;
  tags?: string[];
};

/** Category-aware icon: attack skills read as offense, WSTG as reference,
 * recon as radar, auth/crypto as keys — never a generic file for everything. */
function categoryIcon(skill: SkillLibraryItem) {
  const hay = `${skill.category} ${skill.id} ${(skill.tags ?? []).join(" ")}`.toLowerCase();
  if (/jwt|crypto|auth|token|sso|oauth/.test(hay)) return KeyRound;
  if (/cloud|aws|gcp|azure|k8s|docker/.test(hay)) return Cloud;
  if (/xss|inject|sqli|rce|ssti|deserial|prototype/.test(hay)) return Bug;
  if (/recon|discover|enum|subdomain|fingerprint/.test(hay)) return Radar;
  if (/attack|exploit|bypass|abuse|race|cors|csrf|redirect/.test(hay)) return Zap;
  if (/wstg|method|checklist|guide/.test(hay)) return BookOpen;
  return FileText;
}

const scopeLabel = (scope: string, shared: boolean) => ({
  builtin: "Built in",
  workspace: shared ? "Team" : "Workspace",
  personal: "Solo",
  project: "Project",
  mounted: "Mounted",
})[scope] ?? scope;

/** Inventory stays compact; the selected skill opens in the chat editor.
 * One honest grid — every skill lives in the workspace library, there is no
 * installed/built-in split (that was zero's taxonomy, not Suijin's). */
export function SkillsLibrary({ skills, loading, error, onSelect, onCreate, disabled = false, shared = false, canCreate = true, onRetry }: {
  skills: SkillLibraryItem[];
  loading: boolean;
  error?: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  disabled?: boolean;
  shared?: boolean;
  canCreate?: boolean;
  onRetry?: () => void;
}) {
  const [search, setSearch] = useState("");
  const needle = search.trim().toLocaleLowerCase();
  const visible = skills.filter(skill => `${skill.name} ${skill.description} ${skill.scope} ${skill.category ?? ""} ${(skill.tags ?? []).join(" ")} ${scopeLabel(skill.scope, shared)}`.toLocaleLowerCase().includes(needle));
  const content = loading && !skills.length ? <LoadingState label="Loading skills…" variant="cards" />
    : error ? <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--radius-menu)] bg-destructive/10 p-4 text-sm"><p className="text-destructive">{error}</p>{onRetry && <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={onRetry}>Try again</Button>}</div>
      : visible.length ? <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {visible.map(skill => <Button key={skill.id} type="button" variant="ghost" disabled={disabled} onClick={() => onSelect(skill.id)} aria-label={`Open skill ${skill.name}`} className="group h-auto min-h-44 w-full flex-col items-start justify-start gap-3 whitespace-normal rounded-[var(--radius-panel)] bg-muted/35 p-5 text-left font-normal">
          <span className="flex w-full items-center justify-between gap-3">{(() => { const Icon = categoryIcon(skill); return <Icon aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />; })()}<ArrowUpRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" /></span>
          <span className="block w-full truncate text-sm font-medium" title={skill.name}>{skill.name}</span>
          <span className="line-clamp-2 text-xs leading-5 text-muted-foreground">{skill.description || "Reusable instructions for the agent."}</span>
          <span className="mt-auto flex w-full items-center justify-between gap-3 pt-2 text-xs text-muted-foreground"><span>{scopeLabel(skill.scope, shared)}</span><span className="flex shrink-0 items-center gap-1.5"><FileText aria-hidden="true" className="size-3.5" />{skill.fileCount} {skill.fileCount === 1 ? "file" : "files"}</span></span>
        </Button>)}
      </div> : <div className="flex flex-col items-center gap-3 py-14 text-center">
        <BookOpen aria-hidden="true" className="size-6 text-muted-foreground/60" />
        <p className="text-sm text-muted-foreground">{needle ? "No matching skills." : "No skills yet."}</p>
      </div>;
  return <div className="gap-5">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <p className="text-xs text-muted-foreground">{skills.length} skills in the workspace library.</p>
      <div className="relative w-full sm:w-72"><Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input type="search" aria-label="Search skills" placeholder="Search skills…" value={search} onChange={event => setSearch(event.target.value)} className="pl-9" /></div>
    </div>
    {content}
  </div>;
}
