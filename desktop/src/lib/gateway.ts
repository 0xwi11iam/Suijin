/** Suijin gateway client — typed, generated from the gateway's OpenAPI. */
import type { components } from "./api-types";

export type Status = components["schemas"]["Status"];
export type ToolInfo = components["schemas"]["ToolInfo"];

export interface ApprovalItem {
  id: number;
  command?: string;
  question?: string;
  status: string;
  note?: string;
  [k: string]: unknown;
}

export interface QuestionItem {
  id: number;
  question: string;
  answered: boolean;
  answer?: string;
}

export interface FireteamTask {
  task: string;
  state: "running" | "done" | "queued";
  success: boolean | null;
  steps: number | null;
  findings: string;
}

export interface FireteamTeam {
  team_id: string;
  started: string;
  running: number;
  tasks: FireteamTask[];
}

export type StreamFrame =
  | { kind: "step"; stream: string; entry: Record<string, unknown> }
  | { kind: "cost"; est_cost_usd: number; calls: number; tokens?: number; input_tokens?: number; output_tokens?: number }
  | { kind: "approvals"; items: ApprovalItem[] }
  | { kind: "questions"; items: QuestionItem[] }
  | { kind: "fireteam"; teams: FireteamTeam[]; updated: string };

export class Gateway {
  constructor(private base: string, private token: string) {}

  private headers(): HeadersInit {
    return { Authorization: `Bearer ${this.token}` };
  }

  private async get<T>(path: string): Promise<T> {
    const r = await fetch(`${this.base}${path}`, { headers: this.headers() });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json() as Promise<T>;
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const r = await fetch(`${this.base}${path}`, {
      method: "POST",
      headers: { ...this.headers(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json() as Promise<T>;
  }

  status() {
    return this.get<Status>("/api/status");
  }
  tools() {
    return this.get<ToolInfo[]>("/api/tools");
  }
  usage() {
    return this.get<Record<string, number | boolean | Record<string, unknown>>>("/api/usage");
  }
  findings() {
    return this.get<Record<string, Record<string, unknown>>>("/api/findings");
  }
  spar() {
    return this.get<Record<string, number | string[]>>("/api/spar");
  }
  fireteam() {
    return this.get<{ teams: FireteamTeam[]; updated: string }>("/api/fireteam");
  }
  approvals() {
    return this.get<ApprovalItem[]>("/api/approvals");
  }
  decide(id: number, action: "approve" | "deny", note = "") {
    return this.post<ApprovalItem>(`/api/approvals/${id}`, { action, note });
  }
  questions() {
    return this.get<QuestionItem[]>("/api/questions");
  }
  answer(id: number, answer: string) {
    return this.post<QuestionItem>(`/api/questions/${id}`, { answer });
  }
  engage(objective: string, target = "", template = "", maxCostUsd = 10) {
    return this.post<{ started: boolean; pid: number }>("/api/engage", {
      objective,
      target,
      template,
      max_cost_usd: maxCostUsd,
    });
  }

  /** Verify connectivity + token; throws on failure. */
  async handshake(): Promise<Status> {
    return this.status();
  }

  events(onFrame: (f: StreamFrame) => void, onDown: () => void): WebSocket {
    const ws = new WebSocket(`${this.base.replace(/^http/, "ws")}/events?token=${this.token}`);
    ws.onmessage = (e) => {
      try {
        onFrame(JSON.parse(e.data) as StreamFrame);
      } catch {
        /* malformed frame ignored */
      }
    };
    ws.onclose = onDown;
    ws.onerror = onDown;
    return ws;
  }
}
