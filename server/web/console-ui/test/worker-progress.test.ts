/**
 * What the agents tree says about one agent. Until 2026-11-28 it could say only "Thinking…" for
 * every agent with a heartbeat — including one that had failed every model call and given up.
 */
import { describe, expect, it } from "vitest";
import { workerProgress } from "../src/console/worker-tree";
import type { ConsoleWorker } from "@0/engine/shared";

const worker = (extra: Partial<ConsoleWorker>): ConsoleWorker =>
  ({ id: "agent-1-aa", parentId: "", name: "agent-1-aa", status: "running", task: "", transcript: [], ...extra }) as ConsoleWorker;
const call = (name: string, id = `c-${name}`) => ({ callIndex: 0, call: { id, name, arguments: {} }, result: null as never, running: true });

describe("the line under an agent's name", () => {
  it("says it is thinking only while it is running and has said nothing yet", () => {
    expect(workerProgress(worker({}))).toBe("Thinking…");
  });

  it("shows what the agent is doing: the tool it is running, else its latest thought", () => {
    expect(workerProgress(worker({ transcript: [{ turn: 1, ts: 0, assistant: "Read the bundle", tools: [call("http_get")] }] }))).toBe("Using http get");
    expect(workerProgress(worker({ transcript: [{ turn: 1, ts: 0, assistant: "The surface is a login page;\nthe bundle decides." }] }))).toBe("The surface is a login page;");
  });

  it("shows a failure as it is", () => {
    expect(workerProgress(worker({ status: "failed", error: "Blocked — Unknown Model [called zai:deepseek/x]. Fix the model settings, then send a message to try again." }))).toBe(
      "Blocked — Unknown Model [called zai:deepseek/x]. Fix the model settings, then send a message to try again."
    );
  });

  it("says what an agent that is not running is waiting for, in its own words", () => {
    expect(workerProgress(worker({ status: "parked", task: "paused — holding between turns" }))).toBe("paused — holding between turns");
    expect(workerProgress(worker({ status: "parked", task: "no work queued — send a message to continue" }))).toBe("no work queued — send a message to continue");
  });

  it("keeps 0's words when the agent said nothing", () => {
    expect(workerProgress(worker({ status: "completed" }))).toBe("Complete");
    expect(workerProgress(worker({ status: "parked" }))).toBe("Available");
    expect(workerProgress(worker({ status: "queued" }))).toBe("Starting…");
  });
});
