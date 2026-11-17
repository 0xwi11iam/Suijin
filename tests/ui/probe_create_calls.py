"""Instrumented probe: who POSTs /api/console/sessions and with what body?"""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOTS = Path(__file__).resolve().parent / "shots"
BASE = "http://127.0.0.1:8790"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900}).new_page()
    page.on("request", lambda r: print(f"  >> {r.method} {r.url[len(BASE):][:80]}") if "/api/console" in r.url and r.method == "POST" else None)
    posts = []
    def capture(req):
        if "/api/console/sessions" in req.url and req.method == "POST":
            posts.append((req.url, req.post_data))
    page.on("request", capture)
    page.goto(BASE, wait_until="domcontentloaded")
    t0 = time.time()
    while time.time() - t0 < 8:
        page.wait_for_timeout(500)
    print(f"after load: {len(posts)} create-POSTs")
    for u, b in posts:
        print(f"  body: {b}")
    # now type + enter
    comp = page.get_by_placeholder("Ask Suijin")
    print("composer count:", comp.count())
    if comp.count():
        comp.fill("Probe engagement: fetch the headers of example.com")
        page.wait_for_timeout(300)
        posts.clear()
        comp.press("Enter")
        page.wait_for_timeout(5000)
        print(f"after submit: {len(posts)} create-POSTs, url={page.url}")
        for u, b in posts:
            print(f"  body: {b}")
    browser.close()
