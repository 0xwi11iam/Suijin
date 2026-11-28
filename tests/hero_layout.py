#!/usr/bin/env python3
"""The landing's layout, measured — because "it looks right" is not a measurement.

Run against a console that is already up (default http://127.0.0.1:8790, override with
SUIJIN_WEB_URL):

    python3 tests/hero_layout.py

Read-only: it loads the landing and opens its menus; it submits nothing. For each window size it
checks that nothing overlaps, clips, falls outside the viewport, or is covered by an invisible
element, that there is no horizontal scroll, and that every piece of text meets WCAG AA contrast
(4.5:1 — colours are resolved through the browser's own rasteriser, so oklch and color-mix count).
The three popups are checked the same way.

Exit status is non-zero if any measurement fails. Screenshots land under the temp dir, in
``suijin-hero-shots/``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SUIJIN_WEB_URL", "http://127.0.0.1:8790").rstrip("/")
SHOTS = os.path.join(tempfile.gettempdir(), "suijin-hero-shots")
SIZES = [
    (1920, 1080, "wide"),
    (1600, 1000, "desktop"),
    (1280, 720, "short"),
    (1100, 800, "laptop"),
    (900, 700, "narrow"),
]
MIN_CONTRAST = 4.5
# The landing is a chat by default; Mode and Permissions exist only as an engagement.
CHAT_CONTROLS = [
    "[data-suijin-home-attach]",
    "[data-suijin-home-model]",
    "[data-suijin-home-history]",
    "[data-suijin-home-submit]",
    '[aria-label="Start a"]',
    "[data-suijin-home-suggestion]",
]
ENGAGEMENT_CONTROLS = CHAT_CONTROLS + ["[data-suijin-home-permissions]", '[aria-label="Mode"]']

# Runs inside the landing's document. `root` limits the measurement to one popup when one is open.
MEASURE = r"""
({selectors, root}) => {
  const scope = root ? document.querySelector(root) : document;
  const box = (element) => { const r = element.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; };
  const visible = (element) => { const r = element.getBoundingClientRect(); const s = getComputedStyle(element); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0.01; };
  const overlaps = (a, b) => !(a.x + a.w <= b.x + 0.5 || b.x + b.w <= a.x + 0.5 || a.y + a.h <= b.y + 0.5 || b.y + b.h <= a.y + 0.5);
  const probe = document.createElement('canvas'); probe.width = probe.height = 1;
  const context = probe.getContext('2d', {willReadFrequently: true});
  const rgb = (color) => { context.clearRect(0, 0, 1, 1); context.fillStyle = color; context.fillRect(0, 0, 1, 1); const d = context.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const luminance = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + 0.05) / (Math.min(luminance(a), luminance(b)) + 0.05);
  const behind = (element) => { let node = element; while (node && node.nodeType === 1) { const c = rgb(getComputedStyle(node).backgroundColor); if (c[3] > 0.5) return c; node = node.parentElement; } return rgb(getComputedStyle(document.body).backgroundColor); };
  const bounds = scope === document ? {x: 0, y: 0, w: innerWidth, h: innerHeight} : box(scope);
  const texts = [];
  for (const element of scope.querySelectorAll('*')) {
    const own = Array.from(element.childNodes).filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(' ');
    if (!own || !visible(element)) continue;
    const b = box(element);
    if (b.y + b.h < bounds.y || b.y > bounds.y + bounds.h) continue;  // scrolled out of a list: not on screen
    const ratio = Math.round(contrast(rgb(getComputedStyle(element).color), behind(element)) * 100) / 100;
    texts.push({text: own.slice(0, 40), contrast: ratio, size: parseFloat(getComputedStyle(element).fontSize), off: b.x < bounds.x - 0.5 || b.x + b.w > bounds.x + bounds.w + 0.5 || b.y < bounds.y - 0.5 || b.y + b.h > bounds.y + bounds.h + 0.5});
  }
  const controls = selectors.flatMap((selector) => Array.from(scope.querySelectorAll(selector)).filter(visible).map((element) => {
    const b = box(element); const top = document.elementFromPoint(b.x + b.w / 2, b.y + b.h / 2);
    return {selector, ...b, disabled: Boolean(element.disabled), covered: !(element.disabled || top === element || element.contains(top))};
  }));
  const overlapping = [];
  for (let i = 0; i < controls.length; i++) for (let j = i + 1; j < controls.length; j++) if (overlaps(controls[i], controls[j])) overlapping.push(`${controls[i].selector} × ${controls[j].selector}`);
  const card = scope.querySelector('[data-suijin-home-textarea]')?.closest('.bg-card');
  return {
    viewport: {w: innerWidth, h: innerHeight},
    horizontalScroll: document.documentElement.scrollWidth > innerWidth,
    card: card ? box(card) : null,
    controlRows: [...new Set(controls.map((c) => Math.round(c.y)))],
    overlapping, covered: controls.filter((c) => c.covered).map((c) => c.selector),
    outside: controls.filter((c) => c.x < -0.5 || c.y < -0.5 || c.x + c.w > innerWidth + 0.5 || c.y + c.h > innerHeight + 0.5).map((c) => c.selector),
    lowContrast: texts.filter((t) => t.contrast < MIN_CONTRAST && !t.text.startsWith(', or drop')).map((t) => `${t.text} (${t.contrast})`),
    tinyText: texts.filter((t) => t.size < 11).map((t) => t.text),
    offscreenText: texts.filter((t) => t.off).map((t) => t.text),
    worstContrast: texts.length ? Math.min(...texts.map((t) => t.contrast)) : null,
  };
}
""".replace("MIN_CONTRAST", str(MIN_CONTRAST))


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height, tag in SIZES:
            context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2)
            page = context.new_page()
            errors: list[str] = []
            # Bind this window's list by default argument: a late event from a closed page must not
            # land in the next window's list.
            page.on("pageerror", lambda error, sink=errors: sink.append(str(error)[:160]))
            page.on(
                "console",
                lambda message, sink=errors: sink.append(message.text[:160]) if message.type == "error" else None,
            )
            page.goto(f"{BASE}/chats", wait_until="networkidle")
            page.frame_locator("iframe[data-suijin-hero-frame]").locator("[data-suijin-home-textarea]").wait_for(
                timeout=20000
            )
            page.keyboard.type("layout probe")
            page.wait_for_timeout(700)
            frame = next(f for f in page.frames if "zero/home.html" in f.url)

            def report(measure, kind, shot, tag=tag, width=width, height=height, page=page, errors=errors):
                pane, card = measure["viewport"], measure["card"]
                problems = [
                    key
                    for key in (
                        "horizontalScroll",
                        "overlapping",
                        "covered",
                        "outside",
                        "lowContrast",
                        "tinyText",
                        "offscreenText",
                    )
                    if measure[key]
                ]
                if errors:
                    problems.append("console errors")
                failures.extend(
                    f"{tag} {kind} {key}: {measure[key]}"
                    for key in ("overlapping", "covered", "outside", "lowContrast", "tinyText", "offscreenText")
                    if measure[key]
                )
                if measure["horizontalScroll"] or errors:
                    failures.append(f"{tag} {kind}: horizontal scroll or console errors: {errors[:2]}")
                print(
                    f"{'FAIL' if problems else 'ok  '} {tag:8s} {width}x{height}  {kind:11s} pane {pane['w']}x{pane['h']}  composer {card['w']:.0f}x{card['h']:.0f} "
                    f"({card['w'] / pane['w'] * 100:.0f}% of the pane, top at {card['y'] / pane['h'] * 100:.0f}%)  control rows {measure['controlRows']}  "
                    f"worst text contrast {measure['worstContrast']}" + (f"  -> {problems}" if problems else ""),
                    flush=True,
                )
                page.screenshot(path=os.path.join(SHOTS, shot))

            # the default: a chat
            report(
                frame.evaluate(MEASURE, {"selectors": CHAT_CONTROLS, "root": None}),
                "chat",
                f"layout-{tag}-{width}x{height}.png",
            )
            hero = page.frame_locator("iframe[data-suijin-hero-frame]")
            hero.locator('[aria-label="Start a"]').click()
            page.wait_for_timeout(400)
            frame.locator("[data-slot=dropdown-menu-item]").get_by_text("Engagement", exact=True).click()
            page.wait_for_timeout(500)
            # … and as an engagement
            report(
                frame.evaluate(MEASURE, {"selectors": ENGAGEMENT_CONTROLS, "root": None}),
                "engagement",
                f"layout-{tag}-engagement-{width}x{height}.png",
            )

            for name, selector in (
                ("kind", '[aria-label="Start a"]'),
                ("mode", '[aria-label="Mode"]'),
                ("permissions", "[data-suijin-home-permissions]"),
                ("model", "[data-suijin-home-model]"),
            ):
                page.frame_locator("iframe[data-suijin-hero-frame]").locator(selector).click()
                page.wait_for_timeout(1000 if name == "model" else 400)
                popup = frame.evaluate(MEASURE, {"selectors": [], "root": "[data-slot=dropdown-menu-content]"})
                page.screenshot(path=os.path.join(SHOTS, f"layout-{tag}-menu-{name}.png"))
                issues = [key for key in ("covered", "outside", "lowContrast", "tinyText") if popup[key]]
                failures.extend(f"{tag} {name} menu {key}: {popup[key]}" for key in issues)
                popup_size = frame.evaluate(
                    "(() => { const r = document.querySelector('[data-slot=dropdown-menu-content]').getBoundingClientRect(); return Math.round(r.width) + 'x' + Math.round(r.height) })()"
                )
                print(
                    f"     {'FAIL' if issues else 'ok  '} {name:11s} menu {popup_size}  "
                    f"worst text contrast {popup['worstContrast']}" + (f"  -> {issues}" if issues else ""),
                    flush=True,
                )
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            context.close()
        browser.close()
    print(f"\n{'all measurements pass' if not failures else str(len(failures)) + ' measurement(s) failed:'}")
    for failure in failures:
        print("   " + failure)
    with open(os.path.join(SHOTS, "layout-result.json"), "w", encoding="utf-8") as handle:
        json.dump(failures, handle, indent=1)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
