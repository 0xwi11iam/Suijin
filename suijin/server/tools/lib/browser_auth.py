"""Browser-capture auth — the operator logs in, we capture the session.

On the HOST: a headed Chromium window pops up, the operator logs in
(2FA/CAPTCHA/social auth handled by the human), we capture the cookies
and storage state when the URL changes to a post-login page.

IN THE BOX: the box is headless — the browser can't open there. The
capture runs on the HOST (through the live-mounted repo), opens a GUI
window there, and the captured session is written to the shared
workspace where the box agent picks it up.
"""

from __future__ import annotations

import contextlib
import json
import time


def browser_capture(
    login_url: str,
    credential_name: str,
    target_host: str = "",
    timeout_s: int = 300,
    log=print,
) -> dict:
    """Open a headed Chromium, wait for the operator to log in, capture
    the session. Returns {cookies, storage_state, url, success}.

    The operator sees a real browser window. They log in normally
    (2FA, CAPTCHA, SSO — all handled by the human). When the URL
    changes away from the login page and stabilizes for 3 seconds,
    we capture everything and close.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "success": False,
            "error": "playwright not installed — run: pip install playwright && playwright install chromium",
        }

    # where to write the captured session (shared between host and box
    # through the live-mounted workspace)
    from suijin.modules.platform.lib.workspace import artifact_dir

    auth_dir = artifact_dir("auth")
    auth_dir.mkdir(parents=True, exist_ok=True)
    session_file = auth_dir / f"{credential_name}_session.json"

    log(f"opening browser for {login_url} — log in, we'll capture the session automatically")

    result = {"success": False, "cookies": [], "url": "", "error": ""}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(login_url, timeout=30000)
            log(f"browser open at {login_url}")
            log("log in normally — the session is captured when the page settles after login")

            # wait for the operator to complete login: the URL changes
            # away from the login page and stays stable for 3 seconds
            _initial_path = page.url
            _last_url = page.url
            _stable_since = time.time()
            _deadline = time.time() + timeout_s

            while time.time() < _deadline:
                time.sleep(1)
                current = page.url
                if current != _last_url:
                    _last_url = current
                    _stable_since = time.time()
                    continue
                # URL stable for 3+ seconds AND different from the
                # initial login URL = login complete
                if time.time() - _stable_since >= 3 and current != _initial_path:
                    break
            else:
                result["error"] = f"timeout after {timeout_s}s — session not captured"
                browser.close()
                return result

            # capture everything
            cookies = context.cookies()
            storage = context.storage_state()
            result["cookies"] = [
                {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]}
                for c in cookies
                if target_host in c.get("domain", "") or not target_host
            ]
            result["url"] = page.url
            result["storage_state"] = storage
            result["success"] = len(result["cookies"]) > 0

            # persist for the agent
            session_file.write_text(json.dumps(result, indent=2))
            log(f"session captured: {len(result['cookies'])} cookies, saved to {session_file.name}")

        except Exception as e:
            result["error"] = str(e)
        finally:
            with contextlib.suppress(Exception):
                browser.close()

    return result
