import type { SessionRailRow } from "./session-rail-order";

export function sidebarChatTitle(row: SessionRailRow): string {
  return row.kind === "live" ? row.session.title || row.session.target || "Untitled chat" : row.session.summary || row.session.target || row.session.preview || "Past chat";
}

export interface SidebarChatActivity {
  timestamp: number;
  messageCount: number | undefined;
}

/** Runtime handles and save timestamps change on opening; only new messages change rank. */
export function rememberSidebarActivity(rows: SessionRailRow[], activity: Map<string, SidebarChatActivity>): SessionRailRow[] {
  return rows.map(row => {
    const previous = activity.get(row.key);
    const count = row.session.messageCount;
    const observed = row.kind === "live" ? Date.parse(row.session.updatedAt) || row.timestamp : row.timestamp;
    const timestamp = previous
      ? count !== undefined && previous.messageCount !== undefined && count > previous.messageCount
        ? Math.max(previous.timestamp, observed)
        : previous.timestamp
      : Number.isFinite(row.timestamp) ? row.timestamp : 0;
    activity.set(row.key, {
      timestamp,
      // Retain the high-water mark through shortened/resumed transcript metadata.
      messageCount: count === undefined ? previous?.messageCount : previous?.messageCount === undefined ? count : Math.max(count, previous.messageCount),
    });
    return timestamp === row.timestamp ? row : { ...row, timestamp };
  });
}

/** Sort on remembered conversation activity, never a handle's last-opened timestamp. */
export function sortSidebarChats(rows: SessionRailRow[], sort: "updated" | "name", filter = ""): SessionRailRow[] {
  const needle = filter.trim().toLocaleLowerCase();
  return rows.filter(row => !needle || sidebarChatTitle(row).toLocaleLowerCase().includes(needle)).sort((a, b) => sort === "name" ? sidebarChatTitle(a).localeCompare(sidebarChatTitle(b)) || a.key.localeCompare(b.key) : b.timestamp - a.timestamp || a.key.localeCompare(b.key));
}

const STATUS_WORDS: Record<string, string> = {
  ready: "Ready",
  idle: "Ready", // Suijin's word for a session waiting for its next message; 0's table did not know it, and a same-titled row read "undefined · 6 messages"
  working: "Responding",
  cancelling: "Stopping",
  waiting: "Waiting for you",
  failed: "Needs attention",
  closed: "Closed",
};

/** Where a session files in the sidebar. Not about a target — a question, a tool test, a chat
 * that named no host and started no engagement — files together under one folder, General, so it
 * never clutters the target folders (operator 2026-11-27). Anything about a target files with it,
 * a chat included: the chat that started an engagement sits in that engagement's folder
 * (operator 2026-11-28: it "stayed in general instead of joining or creating a new folder").
 *  The backend decides what a session is about (targetKey); this only groups. */
export function sessionFolder(session: { targetKey?: string; targetLabel?: string; target?: string; kind?: string }): { key: string; label: string; general: boolean } {
  const general = !String(session.targetKey || "").trim();
  const label = general ? "General" : String(session.targetLabel || session.target || "Unsorted target");
  return { key: general ? "general" : String(session.targetKey || label.toLowerCase()), label, general };
}

/** Equal titles can belong to separate transcripts; retain both and explain which is which. */
export function sidebarChatDetails(rows: SessionRailRow[]): Map<string, string> {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const title = sidebarChatTitle(row).trim().toLocaleLowerCase();
    counts.set(title, (counts.get(title) ?? 0) + 1);
  }
  const details = new Map<string, string>();
  for (const row of rows) {
    if ((counts.get(sidebarChatTitle(row).trim().toLocaleLowerCase()) ?? 0) < 2) continue;
    // A status nobody has a word for is shown as it is — never as "undefined".
    const status = row.kind === "saved" ? "Saved" : (STATUS_WORDS[row.session.status] ?? row.session.status);
    details.set(row.key, `${status} · ${row.session.messageCount ?? 0} messages · ${row.key.slice(0, 8)}`);
  }
  return details;
}
