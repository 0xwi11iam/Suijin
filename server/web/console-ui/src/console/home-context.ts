import type { ConsoleSessionSnapshot } from "@0/engine/shared";
import type { ProjectResponse } from "@/components/console-control/contracts";

export interface HomeSuggestion { icon:"code"|"shield"|"plan"; label:string; hint:string; prompt:string }
export function selectedWorkspacePath(snapshot:ConsoleSessionSnapshot):string|undefined {
  const path=snapshot.workspacePath ?? snapshot.execution?.workspacePath;
  return typeof path === "string" && path.trim() ? path : undefined;
}
const comparablePath=(path:string)=>path.replaceAll("\\","/").replace(/\/+$/,"");
/** Metadata is usable only for the explicitly selected folder queried on this chat's host. */
export function homeSuggestions(snapshot:ConsoleSessionSnapshot,metadata?:{requestedPath:string;project:ProjectResponse}):HomeSuggestion[] {
  const finding=snapshot.focusedFinding;
  if(finding)return [
    {icon:"shield",label:"Review this finding",hint:finding.title,prompt:`Review the stored evidence for the focused finding "${finding.title}". Explain what is confirmed and what still needs verification.`},
    {icon:"plan",label:"Plan a verification",hint:"Start with the evidence and scope",prompt:"Propose a bounded verification plan for the focused finding, using its retained evidence and authorized scope."},
    {icon:"code",label:"Explore a fix",hint:"Compare remediation and regression checks",prompt:"Explain remediation options for the focused finding, including a regression check. Start with a proposal."},
  ];
  const workspace=selectedWorkspacePath(snapshot);
  const project=workspace && metadata?.requestedPath===workspace?metadata.project:undefined;
  const pending=snapshot.todos?.todos.find(todo=>!["completed","cancelled"].includes(todo.status));
  if(workspace){
    const path=project?.path || workspace;
    const git=project?.git.root && !project.git.error?project.git:undefined;
    const repoChanges=Boolean(git?.dirty && comparablePath(git.root!)===comparablePath(path));
    const scope=`the selected workspace ${JSON.stringify(path)}`;
    return [
      {icon:"shield",label:"Map the attack surface",hint:git?`${project?.name} · ${git.branch || "Git repository"}`:path,prompt:`Map the attack surface of ${scope}. Enumerate entry points, endpoints and exposed tech, then summarize the most promising areas to probe. Stay in scope and separate observations from hypotheses.`},
      repoChanges?{icon:"code",label:"Hunt the uncommitted changes",hint:"Local changes not yet committed",prompt:`Hunt the uncommitted changes in ${scope} for exploitable paths. Read the diff first, trace inputs to sinks, and report each candidate with the evidence that supports or refutes it.`}:{icon:"code",label:"Hunt for exploitable paths",hint:"Inputs to sinks, reachable first",prompt:`Hunt ${scope} for exploitable paths. Trace inputs to sinks, prioritize reachable code, and report each candidate with evidence.`},
      {icon:"plan",label:"Plan the swarm hunt",hint:"Lanes, turn caps, guardrails",prompt:`Plan a swarm hunt of ${scope}. Propose lanes (recon, exploit, fuzz), turn caps and transport guardrails before anything is sent.`},
    ];
  }
  const target=snapshot.localScopePath || snapshot.session.target;
  if(target)return [
    {icon:"shield",label:"Review this target",hint:target,prompt:`Review ${JSON.stringify(target)} for security issues. Start by identifying the authorized scope and evidence to inspect.`},
    {icon:"code",label:"Inspect the available evidence",hint:"Separate observations from hypotheses",prompt:`Inspect the available evidence for ${JSON.stringify(target)} and distinguish verified issues from unconfirmed concerns.`},
    {icon:"plan",label:"Plan a focused review",hint:"Establish useful checks and limits",prompt:`Create a focused security review plan for ${JSON.stringify(target)}, with explicit scope, time and cost limits before execution.`},
  ];
  return [
    {icon:"code",label:"Whitebox pentest",hint:"Choose a source repository",prompt:"Help me start a whitebox pentest. First ask me to choose the source repository or workspace folder and establish its scope."},
    {icon:"shield",label:"Investigate a question",hint:"Start from evidence and constraints",prompt:"Help me investigate a security question. Ask for the relevant evidence and constraints first."},
    {icon:"plan",label:"Plan an automation",hint:"Choose a target and useful checks",prompt:"Help me plan a security automation with a target, authorized scope, and time and cost limits."},
  ];
}
