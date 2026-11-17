"""Trace which code paths POST empty session creates on page load."""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8790"

INIT = """
window.__postStacks = [];
const _orig = window.fetch;
window.fetch = async function(...args) {
  const [res, init] = args;
  const url = typeof res === 'string' ? res : res.url;
  const method = (init && init.method) || (res && res.method) || 'GET';
  if (url.includes('/api/console/sessions') && method === 'POST') {
    const body = init && init.body;
    window.__postStacks.push({ url, body: String(body || ''), stack: new Error().stack });
  }
  return _orig.apply(this, args);
};
"""

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900}).new_page()
    page.add_init_script(INIT)
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_timeout(8000)
    stacks = page.evaluate("window.__postStacks")
    print(f"{len(stacks)} create-POSTs:")
    for s in stacks:
        print(f"  body: {s['body'][:60]}")
        frames = [f for f in (s['stack'] or '').split('\n') if 'assets/' in f or 'console-page' in f][:4]
        for f in frames:
            print(f"    {f.strip()[:160]}")
    browser.close()
