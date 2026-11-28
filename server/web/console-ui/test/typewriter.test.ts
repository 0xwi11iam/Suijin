import { describe, expect, it } from "vitest";
import {
  REASONING_CHARS_PER_SECOND,
  REPLY_CHARS_PER_SECOND,
  markTurnWorking,
  nextShown,
  shouldAnimate,
  shownSlice,
  wasWorkingRecently,
} from "../src/console/typewriter";

describe("nextShown", () => {
  it("advances toward the target at the given pace, and never past it", () => {
    expect(nextShown(0, 100, 0.5, 200)).toBe(100);
    expect(nextShown(0, 1000, 0.5, 200)).toBe(100);
    expect(nextShown(900, 1000, 0.5, 200)).toBe(1000);
  });

  it("never goes backwards when the text is replaced by a shorter one", () => {
    expect(nextShown(500, 120, 0.1, 200)).toBe(120);
  });

  it("a zero or negative step leaves the reveal where it is", () => {
    expect(nextShown(40, 100, 0, 200)).toBe(40);
  });
});

describe("shouldAnimate", () => {
  const base = { reducedMotion: false, history: false } as const;

  it("types text that belongs to a turn that is working", () => {
    expect(shouldAnimate({ ...base, fresh: true, textLength: 400 })).toBe(true);
  });

  it("shows text from history at once — nothing types on a reload", () => {
    expect(shouldAnimate({ ...base, fresh: false, textLength: 4000 })).toBe(false);
  });

  it("shows text that appears together with the transcript at once — opening a chat mid-turn must not retype it", () => {
    expect(shouldAnimate({ reducedMotion: false, history: true, fresh: true, textLength: 4000 })).toBe(false);
  });

  it("never animates under reduced motion", () => {
    expect(shouldAnimate({ reducedMotion: true, history: false, fresh: true, textLength: 400 })).toBe(false);
  });

  it("nothing to type when the text is empty or already tiny", () => {
    expect(shouldAnimate({ ...base, fresh: true, textLength: 0 })).toBe(false);
    expect(shouldAnimate({ ...base, fresh: true, textLength: 12 })).toBe(false);
  });
});

describe("turn recency", () => {
  it("a turn counts as fresh while it works and for a moment after — a final reply lands in the same poll as 'done'", () => {
    expect(wasWorkingRecently("turn-a")).toBe(false);
    markTurnWorking("turn-a");
    expect(wasWorkingRecently("turn-a")).toBe(true);
    expect(wasWorkingRecently("turn-b")).toBe(false);
  });
});

describe("shownSlice", () => {
  it("returns the whole text once fully revealed, and a prefix before that", () => {
    expect(shownSlice("hello", 5)).toBe("hello");
    expect(shownSlice("hello", 2)).toBe("he");
    expect(shownSlice("hello", 0)).toBe("");
  });

  it("does not cut inside a surrogate pair (emoji, CJK extensions)", () => {
    const text = "a😀b";
    expect(shownSlice(text, 2)).toBe("a");
    expect(shownSlice(text, 3)).toBe("a😀");
  });
});

describe("paces", () => {
  it("reasoning reads slower than the reply, and both finish a normal message in a few seconds", () => {
    expect(REASONING_CHARS_PER_SECOND).toBeLessThan(REPLY_CHARS_PER_SECOND);
    expect(600 / REASONING_CHARS_PER_SECOND).toBeLessThan(4);
    expect(2000 / REPLY_CHARS_PER_SECOND).toBeLessThan(4);
  });
});
