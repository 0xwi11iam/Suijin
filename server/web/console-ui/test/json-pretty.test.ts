import { describe, expect, it } from "vitest";
import {
  maskSecrets,
  parseJsonText,
  summarizeJson,
  tokenizeJson,
  toJsonText,
} from "../src/console/json-pretty";

describe("parseJsonText", () => {
  it("parses plain JSON", () => {
    expect(parseJsonText('{"a": 1}')?.value).toEqual({ a: 1 });
    expect(parseJsonText("  [1, 2] ")?.value).toEqual([1, 2]);
  });

  it("parses what tools actually print: a fenced block", () => {
    expect(parseJsonText('Here you go:\n```json\n{"ok": true}\n```')?.value).toEqual({ ok: true });
  });

  it("parses JSON behind a prefix and a trailing note", () => {
    const found = parseJsonText('RESULT: {"n": 2, "nested": {"x": [1, {"y": "}"}]}}\nDone.');
    expect(found?.value).toEqual({ n: 2, nested: { x: [1, { y: "}" }] } });
  });

  it("is not fooled by braces inside strings", () => {
    expect(parseJsonText('{"code": "if (a) { return; }", "n": 1}')?.value).toEqual({ code: "if (a) { return; }", n: 1 });
  });

  it("leaves alone what is not a JSON container", () => {
    expect(parseJsonText("plain prose about {braces")).toBeNull();
    expect(parseJsonText("42")).toBeNull();
    expect(parseJsonText('"just a string"')).toBeNull();
    expect(parseJsonText("")).toBeNull();
  });

  it("reports where the JSON sits in the text", () => {
    const text = 'RESULT: {"a": 1, "b": 2} trailing';
    const found = parseJsonText(text);
    expect(found && text.slice(found.start, found.end)).toBe('{"a": 1, "b": 2}');
  });
});

describe("toJsonText", () => {
  it("pretty-prints with two-space indentation and keeps the key order", () => {
    expect(toJsonText({ z: 1, a: { b: [1, 2] } })).toBe('{\n  "z": 1,\n  "a": {\n    "b": [\n      1,\n      2\n    ]\n  }\n}');
  });

  it("round-trips a value that was parsed from text", () => {
    const found = parseJsonText('{"b":1,"a":[true,null]}');
    expect(JSON.parse(toJsonText(found!.value))).toEqual({ b: 1, a: [true, null] });
  });
});

describe("tokenizeJson", () => {
  const types = (text: string) => tokenizeJson(text).filter((token) => token.type !== "whitespace").map((token) => `${token.type}:${token.value}`);

  it("separates keys, strings, numbers, literals and punctuation", () => {
    expect(types('{"n": -1.5e3, "s": "x\\\"y", "t": true, "z": null}')).toEqual([
      "punct:{", "key:\"n\"", "punct::", "number:-1.5e3", "punct:,",
      "key:\"s\"", "punct::", "string:\"x\\\"y\"", "punct:,",
      "key:\"t\"", "punct::", "boolean:true", "punct:,",
      "key:\"z\"", "punct::", "null:null", "punct:}",
    ]);
  });

  it("a key is a string followed by a colon; the same text elsewhere is a string", () => {
    expect(types('{"a": "b"}')).toEqual(["punct:{", "key:\"a\"", "punct::", "string:\"b\"", "punct:}"]);
  });

  it("keeps whitespace so the tokens reassemble into the original", () => {
    const text = toJsonText({ a: [1, { b: "c c" }], d: false });
    expect(tokenizeJson(text).map((token) => token.value).join("")).toBe(text);
  });

  it("does not treat a colon inside a string as a key marker", () => {
    expect(types('{"url": "https://example.test/x"}')).toEqual([
      "punct:{", "key:\"url\"", "punct::", "string:\"https://example.test/x\"", "punct:}"]);
  });
});

describe("summarizeJson", () => {
  it("describes a container for the viewer's header", () => {
    expect(summarizeJson({ a: 1, b: 2 })).toEqual({ kind: "object", label: "2 keys" });
    expect(summarizeJson([1, 2, 3])).toEqual({ kind: "array", label: "3 items" });
    expect(summarizeJson([])).toEqual({ kind: "array", label: "empty" });
    expect(summarizeJson({})).toEqual({ kind: "object", label: "empty" });
  });
});

describe("maskSecrets", () => {
  it("masks credential-shaped keys wherever they are nested, and keeps the key names", () => {
    const masked = maskSecrets({ provider: "x", apiKey: "sk-live-123", nested: { ZAI_API_KEY: "abc", token: "t", Authorization: "Bearer xyz" } });
    expect(masked).toEqual({ provider: "x", apiKey: "••••••", nested: { ZAI_API_KEY: "••••••", token: "••••••", Authorization: "••••••" } });
  });

  it("does not touch settings that merely contain the word token or key", () => {
    const config = { max_tokens: 8000, model_key: "m1", context_tokens: 200000, keyboard: true, keyEnv: "NUMES_API_KEY", token_count: 3, key: "zai" };
    expect(maskSecrets(config)).toEqual(config);
  });

  it("masks a setting whose name ends in a secret word, however it is spelled", () => {
    expect(maskSecrets({ neo4j_password: "hunter2", custom_providers: [{ name: "lab", api_key: "qrstuvwx123456" }], OPENAI_API_KEY: "abc", auth_token: "t" }))
      .toEqual({ neo4j_password: "••••••", custom_providers: [{ name: "lab", api_key: "••••••" }], OPENAI_API_KEY: "••••••", auth_token: "••••••" });
  });

  it("does not mistake a bracketed fragment inside source code for JSON", () => {
    const python = [
      "from suijin.server.web import data_hub",
      "",
      'stores = data_hub.catalog()',
      'agents = data_hub.read("swarm.agents")["rows"]    # rows, as plain dicts',
      'open_work = data_hub.read("swarm.intents", state="open", limit=20)',
      "",
      "def changed(store_id, payload):",
      '    print(store_id, "now has", payload.get("rows"), "rows")',
      "",
      "unwatch = data_hub.watch(changed)",
    ].join("\n");
    expect(parseJsonText(python)).toBeNull();
    expect(parseJsonText("const config = {max_tokens: 8000}; const limits = [1000, 2000];")).toBeNull();   // JS object literals are not JSON
    expect(parseJsonText("See the list [1, 2, 3] and the mapping {a: b} in the docs.")).toBeNull();
  });

  it("still finds JSON that has a short label in front of it and a note after it", () => {
    const found = parseJsonText('RESULT: {"ok": true, "rows": 3}\nDone in 2.1s');
    expect(found?.value).toEqual({ ok: true, rows: 3 });
    expect("RESULT: {\"ok\": true, \"rows\": 3}\nDone in 2.1s".slice(0, found!.start)).toBe("RESULT: ");
    expect("RESULT: {\"ok\": true, \"rows\": 3}\nDone in 2.1s".slice(found!.end)).toBe("\nDone in 2.1s");
  });

  it("masks credential-shaped strings inside tool output (a bearer header, a cookie)", () => {
    const masked = maskSecrets({ output: "Authorization: Bearer eyJhbGciOi.abc.def\nCookie: sid=secret123; other=1" });
    expect(masked.output).not.toMatch(/eyJhbGciOi|secret123/);
    expect(masked.output).toMatch(/Bearer ••••••/);
    expect(masked.output).toMatch(/sid=••••••/);
  });

  it("never mutates the value it is given", () => {
    const value = { apiKey: "x" };
    maskSecrets(value);
    expect(value.apiKey).toBe("x");
  });
});
