/** The swarm list's little rules: lanes parse, status colour. */
import { describe, expect, it } from "vitest";
import { parseLanes, swarmTone } from "../src/ops/swarms";

describe("parseLanes", () => {
  it("splits lanes by line and drops empties", () => {
    expect(parseLanes("map the edge\n\nprobe the API\n")).toEqual(["map the edge", "probe the API"]);
  });
  it("caps at sixteen lanes", () => {
    expect(parseLanes(Array.from({ length: 30 }, (_, i) => `lane ${i}`).join("\n")).length).toBe(16);
  });
});

describe("swarmTone", () => {
  it("running is emerald, paused amber, legacy neutral", () => {
    expect(swarmTone("running")).toBe("emerald");
    expect(swarmTone("paused")).toBe("amber");
    expect(swarmTone("defining")).toBe("sky");
    expect(swarmTone("stopped")).toBe("violet");
    expect(swarmTone("done")).toBe("neutral");
    expect(swarmTone("legacy")).toBe("neutral");
  });
});
