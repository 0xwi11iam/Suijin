/**
 * JSON a model pastes into an answer or its thinking renders as a tree — the same viewer a tool's
 * output gets. Prose stays prose, and a paragraph that is still arriving is not turned into one.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Markdown } from "../src/console/markdown";

const render = (text: string, streaming = false) => renderToStaticMarkup(createElement(Markdown, { text, streaming }));
const trees = (html: string) => (html.match(/data-json-kind="tree"/g) ?? []).length;

describe("JSON inside Markdown", () => {
  it("shows a paragraph that is only a JSON object as a tree", () => {
    const html = render('{"ok": true, "items": [1, 2], "nested": {"token_count": 3}}');
    expect(trees(html)).toBe(1);
    expect(html).toContain('class="json-key"');
    expect(html).toContain("token_count");
  });

  it("shows a paragraph that is only a JSON array as a tree", () => {
    expect(trees(render('[{"id": 1}, {"id": 2}]'))).toBe(1);
  });

  it("leaves prose alone", () => {
    const html = render("Suijin is healthy. The coordinator is not running, and no provider has been verified.");
    expect(trees(html)).toBe(0);
    expect(html).toContain("Suijin is healthy.");
  });

  it("leaves a paragraph alone when JSON is only part of it", () => {
    expect(trees(render('{"ok": true} is what the tool returned.'))).toBe(0);
    expect(trees(render("The answer: {\"ok\": true}"))).toBe(0);
  });

  it("keeps a fenced block as a highlighted code block, as before", () => {
    const html = render('```json\n{"ok": true}\n```');
    expect(trees(html)).toBe(0);
    expect(html).toContain("json");
  });

  it("does not turn a paragraph that is still arriving into a tree", () => {
    expect(trees(render('{"ok": true, "items": [1, 2', true))).toBe(0);          // incomplete
    expect(trees(render('{"ok": true, "items": [1, 2]}', true))).toBe(0);         // complete, but still streaming
    expect(trees(render('{"ok": true, "items": [1, 2]}'))).toBe(1);              // once it has landed
  });

  it("masks secrets inside the pasted JSON", () => {
    const html = render('{"api_key": "abcdefghijklmnop", "max_tokens": 8000}');
    expect(html).not.toContain("abcdefghijklmnop");
    expect(html).toContain("max_tokens");
  });

  it("renders multi-line JSON the way a model pastes it", () => {
    const text = '{\n  "status": "ok",\n  "checks": ["a", "b"]\n}';
    expect(trees(render(text))).toBe(1);
  });
});
