/**
 * The sidebar's filing and its disambiguation line, pinned. Both were inferred from screenshots
 * before: a row reading "undefined · 6 messages" (Suijin's `idle` status is not in 0's table), and
 * a no-target run that a journey expected to see although blank sessions are hidden by design.
 */
import { describe, expect, it } from "vitest";
import { sessionFolder, sidebarChatDetails } from "../src/console/sidebar-chat-order";
import type { SessionRailRow } from "../src/console/session-rail-order";

type Session = Extract<SessionRailRow, { kind: "live" }>["session"];

function live(id: string, title: string, extra: Partial<Session> = {}): SessionRailRow {
  return { key: id, kind: "live", timestamp: 1, session: { id, title, status: "idle", messageCount: 6, updatedAt: "2026-11-28T00:00:00Z", ...extra } as Session } as SessionRailRow;
}

describe("where a session files", () => {
  const folder = (session: Record<string, unknown>) => sessionFolder(session as never);

  it("files a chat under General, whatever its title says", () => {
    expect(folder({ kind: "chat", targetKey: "", targetLabel: "" })).toMatchObject({ key: "general", label: "General", general: true });
    expect(folder({ kind: "chat", targetKey: "shop.example", targetLabel: "shop.example" }).general).toBe(true);
  });

  it("files a run that is not about a target under General too", () => {
    expect(folder({ kind: "engagement", targetKey: "", targetLabel: "" })).toMatchObject({ key: "general", label: "General", general: true });
    expect(folder({ kind: "engagement", targetKey: "   ", targetLabel: "" }).general).toBe(true);
  });

  it("files a run about a target under that target", () => {
    expect(folder({ kind: "engagement", targetKey: "shop.example", targetLabel: "shop.example" })).toEqual({ key: "shop.example", label: "shop.example", general: false });
    expect(folder({ kind: "engagement", targetKey: "k", target: "shop.example" }).label).toBe("shop.example");
    expect(folder({ kind: "engagement", targetKey: "k" }).label).toBe("Unsorted target");
  });
});

describe("the line that tells two same-titled chats apart", () => {
  it("names every status Suijin uses — never 'undefined'", () => {
    const statuses = ["idle", "ready", "working", "cancelling", "waiting", "failed", "closed"];
    const rows = statuses.map((status, index) => live(`session-${index}`, "Use the suijin_doctor tool", { status: status as Session["status"] }));
    const details = [...sidebarChatDetails(rows).values()];
    expect(details.every(line => !line.includes("undefined"))).toBe(true);
    expect(details[0]).toBe("Ready · 6 messages · session-");     // idle: ready for the next message
    expect(details[1]).toBe("Ready · 6 messages · session-");
    expect(details[2]?.startsWith("Responding ·")).toBe(true);
  });

  it("says nothing when a title is unique", () => {
    expect(sidebarChatDetails([live("a", "one"), live("b", "two")]).size).toBe(0);
  });
});
