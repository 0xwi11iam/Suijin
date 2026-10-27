"""Browser-capture auth — the operator logs in, we grab EVERYTHING.

Opens a headed Chromium window on the HOST. The operator logs in
normally (2FA/CAPTCHA/SSO handled by the human). We:
  1. intercept ALL network responses and capture every Set-Cookie header
  2. poll the cookie jar every second
  3. when the operator is done (browser closes or they press the capture
     hotkey or the URL moves away from login and stabilizes), we grab
     the ENTIRE browser state — cookies, localStorage, sessionStorage

No URL-pattern matching, no domain filtering, no heuristics. Everything
the browser saw goes to the agent.
"""

from __future__ import annotations

import contextlib
import json
import time


def _launch_chromium(p, log):
    """Launch headed chromium, self-installing the browser ONCE when the
    pinned build is missing (2026-10-27).

    Every playwright package version pins a different chromium build
    (1228/1243/1248/...) and the cache holds whichever builds were ever
    downloaded — so a pip upgrade orphans the launcher. Probe the pinned
    path; if absent, install it via THIS interpreter's playwright (stdin
    never inherited; container-safe), then launch. If it still fails,
    degrade to an actionable error instead of a stack trace."""
    import os
    import subprocess
    import sys

    try:
        pinned = str(p.chromium.executable_path)
    except Exception:  # noqa: BLE001 — unresolvable path: let launch() speak
        pinned = ""
    if pinned and not os.path.exists(pinned):
        log(f"chromium build missing ({pinned.rsplit('/', 1)[-1]}) — one-time download…")
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=600,
                check=False,
            )
        except Exception as e:  # noqa: BLE001 — install failure falls through to launch()
            log(f"auto-install skipped: {e}")
    try:
        return p.chromium.launch(headless=False)
    except Exception as e:
        if "Executable doesn't exist" in str(e):
            raise RuntimeError(
                f"chromium not installed for this interpreter ({sys.executable}). "
                "Run: python -m playwright install chromium"
            ) from e
        raise


def browser_capture(
    login_url: str,
    credential_name: str,
    target_host: str = "",
    timeout_s: int = 300,
    log=print,
) -> dict:
    """Open a headed Chromium, wait for login, capture EVERYTHING.

    Returns {cookies, storage_state, url, success, localStorage}.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "error": "playwright not installed"}

    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    auth_dir = WORKSPACE_DIR / "artifacts" / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    session_file = auth_dir / f"{credential_name}_session.json"

    result = {"success": False, "cookies": [], "url": "", "error": "", "localStorage": {}}
    all_cookies = []  # every cookie the browser EVER saw (network + jar)

    log(f"opening browser for {login_url}")
    log("log in normally — everything the browser captures flows to the agent")

    with sync_playwright() as p:
        browser = _launch_chromium(p, log)
        context = browser.new_context()
        page = context.new_page()

        # 1. INTERCEPT ALL NETWORK RESPONSES — capture every Set-Cookie
        captured_headers = []

        def on_response(response):
            with contextlib.suppress(Exception):
                for header_name, header_value in response.headers_array():
                    if header_name["name"].lower() == "set-cookie":
                        captured_headers.append(header_value)

        page.on("response", on_response)

        try:
            page.goto(login_url, timeout=30000)
            initial_url = page.url
            log(f"browser open at {login_url}")
            log("log in — the session captures when you're done")

            # 2. WAIT: poll the cookie jar every second; capture when
            #    (a) new cookies appear AND the URL has moved from login
            #    (b) OR the operator closes the browser
            #    (c) OR the timeout hits (grab whatever we have)
            deadline = time.time() + timeout_s
            done = False

            while time.time() < deadline and not done:
                time.sleep(1)

                with contextlib.suppress(Exception):
                    if not page.is_visible("body"):
                        break  # browser closed by operator

                    jar = context.cookies()
                    url_moved = page.url != initial_url
                    has_session = len(jar) > 0

                    # capture when the URL moved away from login AND
                    # we have cookies AND things have been stable 3s
                    if url_moved and has_session:
                        # give it 3 seconds to settle (redirects, 2FA flows)
                        time.sleep(3)
                        with contextlib.suppress(Exception):
                            jar = context.cookies()  # re-grab after settle
                            if page.url == initial_url:
                                continue  # went back — not done yet
                        done = True
                        break

            # 3. GRAB EVERYTHING — cookies, localStorage, sessionStorage,
            #    network Set-Cookie headers, final URL
            with contextlib.suppress(Exception):
                jar = context.cookies()
                all_cookies = [
                    {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]} for c in jar
                ]
                for hdr in captured_headers:
                    parts = hdr.split(";")[0].split("=", 1)
                    if len(parts) == 2:
                        name, value = parts
                        if not any(c["name"] == name for c in all_cookies):
                            all_cookies.append({"name": name, "value": value, "domain": "", "path": "/"})

                result["cookies"] = all_cookies
                result["url"] = page.url
                with contextlib.suppress(Exception):
                    result["localStorage"] = json.loads(page.evaluate("() => JSON.stringify(localStorage)") or "{}")
                with contextlib.suppress(Exception):
                    result["sessionStorage"] = json.loads(page.evaluate("() => JSON.stringify(sessionStorage)") or "{}")
                result["networkCookies"] = captured_headers

                # EMPTY CAPTURES ARE FAILURES (2026-10-27, direct-test
                # catch): closing the browser without logging in used to
                # report success=True with 0 cookies — request_auth then
                # announced "AUTH CAPTURED", stored an empty credential,
                # and handed the agent a dead env var. A capture that
                # captured nothing is a failure.
                captured_anything = bool(all_cookies or result.get("localStorage") or result.get("sessionStorage"))
                if captured_anything:
                    result["success"] = True
                    session_file.write_text(json.dumps(result, indent=2))
                    log(
                        f"captured: {len(all_cookies)} cookies, "
                        f"{len(result.get('localStorage', {}))} localStorage keys, "
                        f"{len(result.get('sessionStorage', {}))} sessionStorage keys, "
                        f"{len(captured_headers)} network Set-Cookie headers"
                    )
                else:
                    result["success"] = False
                    result["error"] = (
                        "nothing captured — the login never completed (no cookies, no storage "
                        "at capture time). Re-run and finish the sign-in in the browser window."
                    )
                    log(result["error"])

        except Exception as e:
            # browser closed or crashed — grab whatever we still have
            with contextlib.suppress(Exception):
                jar = context.cookies()
                if jar:
                    result["cookies"] = [
                        {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]} for c in jar
                    ]
                    result["success"] = True
                    result["url"] = page.url if page else ""
                    session_file.write_text(json.dumps(result, indent=2))
                    log(f"browser closed — captured {len(result['cookies'])} cookies")
            if not result.get("success"):
                result["error"] = str(e)
        finally:
            with contextlib.suppress(Exception):
                browser.close()

    return result
