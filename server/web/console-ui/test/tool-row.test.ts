import { describe, expect, it } from "vitest";
import { toolRowTitle, exploitBoxFromResult, filePathFromCall } from "../src/console/tool-row";

describe("toolRowTitle", () => {
  it("names the terminal what it is", () => {
    expect(toolRowTitle({ name: "execute_terminal", arguments: "{}", result: "ok" }, {})).toBe("Executed Terminal");
  });
  it("uses the curated label the backend serves", () => {
    expect(toolRowTitle({ name: "crtsh", arguments: "{}", result: "ok" }, { crtsh: "CRT.sh" })).toBe("Used CRT.sh");
    expect(toolRowTitle({ name: "gobuster_dir", arguments: "{}", result: "ok" }, { gobuster_dir: "Gobuster" })).toBe("Used Gobuster");
  });
  it("falls back to the raw name until labels arrive", () => {
    expect(toolRowTitle({ name: "crtsh", arguments: "{}", result: "ok" }, {})).toBe("Used crtsh");
  });
  it("pack tools drop their action suffix in the fallback", () => {
    expect(toolRowTitle({ name: "nmap_scan", arguments: "{}", result: "ok" }, {})).toBe("Used nmap");
    expect(toolRowTitle({ name: "nikto_scan", arguments: "{}", result: "ok" }, {})).toBe("Used nikto");
    expect(toolRowTitle({ name: "gobuster_dir", arguments: "{}", result: "ok" }, {})).toBe("Used gobuster");
  });
  it("says running, failed and stopped with the same white words", () => {
    expect(toolRowTitle({ name: "crtsh", arguments: "{}", result: undefined, isRunning: true }, { crtsh: "CRT.sh" })).toBe("Running CRT.sh");
    expect(toolRowTitle({ name: "crtsh", arguments: "{}", result: "boom", status: "Error" }, { crtsh: "CRT.sh" })).toBe("Failed CRT.sh");
    expect(toolRowTitle({ name: "crtsh", arguments: "{}", result: "", status: "Stopped" }, { crtsh: "CRT.sh" })).toBe("Stopped CRT.sh");
  });
  it("read and wrote file are verbs, not Used", () => {
    expect(toolRowTitle({ name: "read_file", arguments: "{}", result: "x" }, {})).toBe("Read file");
    expect(toolRowTitle({ name: "write_file", arguments: "{}", result: "x" }, {})).toBe("Wrote file");
    expect(toolRowTitle({ name: "catalog_exploit", arguments: "{}", result: "x" }, {})).toBe("Catalogued exploit");
  });
});

describe("filePathFromCall — read/wrote file's dropdown shows the path", () => {
  it("takes the path from the arguments", () => {
    expect(filePathFromCall({ name: "write_file", arguments: '{"path": "tmp/exp-002.yaml", "content": "x"}', result: "ok" })).toBe("tmp/exp-002.yaml");
    expect(filePathFromCall({ name: "read_file", arguments: '{"file_path": "inbox/notes.md"}', result: "ok" })).toBe("inbox/notes.md");
  });
  it("is only for the file tools", () => {
    expect(filePathFromCall({ name: "crtsh", arguments: '{"path": "/x"}', result: "ok" })).toBeUndefined();
  });
});

describe("exploitBoxFromResult — the one box a confirmed exploit earns", () => {
  it("reads the verdict line the cataloguer returns", () => {
    const box = exploitBoxFromResult("EXP-002 CONFIRMED — MEDIUM CVSS 5.4 : SSO logout returnTo leaves the session alive (target auth.example.com)");
    expect(box).toEqual({ expId: "EXP-002", severity: "MEDIUM", cvss: "5.4", title: "SSO logout returnTo leaves the session alive", target: "auth.example.com" });
  });
  it("stays quiet unless it is CONFIRMED", () => {
    expect(exploitBoxFromResult("EXP-002 NO — the marker was not in the output")).toBeUndefined();
  });
});
