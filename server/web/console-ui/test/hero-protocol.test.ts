import { describe, expect, it } from "vitest";
import {
  HERO_MAX_FILES,
  HERO_MAX_TEXT,
  HERO_PERMISSIONS,
  isHeroMode,
  isHeroPermissions,
  parseHeroSubmit,
  type HeroMode,
  type HeroPermissions,
} from "../zero/entry/protocol";

const fallback = { permissions: "exploitation" as HeroPermissions, mode: "vdp" as HeroMode };

const file = (name: string) => new File(["scope"], name, { type: "text/markdown" });

describe("parseHeroSubmit", () => {
  it("passes through a well-formed submission", () => {
    const scope = file("scope.md");
    expect(
      parseHeroSubmit(
        { type: "suijin:hero:submit", text: "  Map example.test  ", permissions: "recon", mode: "pentest", files: [scope] },
        fallback,
      ),
    ).toEqual({ type: "suijin:hero:submit", text: "Map example.test", permissions: "recon", mode: "pentest", files: [scope] });
  });

  it("refuses anything that is not a non-empty submission", () => {
    expect(parseHeroSubmit(null, fallback)).toBeNull();
    expect(parseHeroSubmit("text", fallback)).toBeNull();
    expect(parseHeroSubmit({ type: "suijin:hero:submit", text: "   " }, fallback)).toBeNull();
    expect(parseHeroSubmit({ type: "suijin:hero:submit", text: 42 }, fallback)).toBeNull();
    expect(parseHeroSubmit({ type: "something-else", text: "hi" }, fallback)).toBeNull();
  });

  it("falls back to the console's own selection when a selector is unknown", () => {
    const parsed = parseHeroSubmit({ type: "suijin:hero:submit", text: "hi", permissions: "root", mode: "yolo" }, fallback);
    expect(parsed).toMatchObject({ permissions: "exploitation", mode: "vdp" });
  });

  it("clamps an oversized prompt and an oversized file list", () => {
    const parsed = parseHeroSubmit(
      {
        type: "suijin:hero:submit",
        text: "x".repeat(HERO_MAX_TEXT + 500),
        permissions: "maximum",
        mode: "ctf",
        files: Array.from({ length: HERO_MAX_FILES + 5 }, (_, index) => file(`scope-${index}.md`)),
      },
      fallback,
    );
    expect(parsed?.text).toHaveLength(HERO_MAX_TEXT);
    expect(parsed?.files).toHaveLength(HERO_MAX_FILES);
  });

  it("keeps only real File objects out of a files field", () => {
    const scope = file("scope.md");
    const parsed = parseHeroSubmit(
      { type: "suijin:hero:submit", text: "hi", permissions: "recon", mode: "vdp", files: [scope, "scope.md", null, 7, { name: "fake" }] },
      fallback,
    );
    expect(parsed?.files).toEqual([scope]);
    expect(parseHeroSubmit({ type: "suijin:hero:submit", text: "hi", files: "not-a-list" }, fallback)?.files).toEqual([]);
  });
});

describe("selector guards", () => {
  it("accept exactly the documented values and nothing else", () => {
    for (const value of HERO_PERMISSIONS) expect(isHeroPermissions(value)).toBe(true);
    expect(isHeroPermissions("root")).toBe(false);
    expect(isHeroPermissions(undefined)).toBe(false);
    expect(isHeroMode("pentest")).toBe(true);
    for (const value of ["vdp ", "VDP", "", null, 0]) expect(isHeroMode(value)).toBe(false);
  });
});
