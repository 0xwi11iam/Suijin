"""`suijin auth <target>` — named browser sessions, one command.

Two ways in, one artifact out:

  paste mode   operator pastes a cookie string (from devtools or an
               existing session) — parsed prefix-safe, becomes a
               playwright storage_state AND a shared-store credential
  browser mode blank-slate visible Chromium — the operator navigates
               and logs in (2FA/SSO/captcha all fine, it's a human),
               presses Enter in the terminal, the session jar is
               captured via storage_state

The storage_state file is what browser contexts are BORN with; the
credential registration is what http_request/http_replay attach. One
login, both worlds, zero retyping.

Prefix handling matters (proven by test, 2026-11-06): Chromium rejects
__Secure-/__Host- cookies unless secure=True is set, and __Host- must
not carry a domain (url-form instead). Google's auth jar is entirely
__Secure-/__Host- prefixed — naive pasting breaks on exactly the
targets that matter.

Session files are local-only (~/.suijin/config/sessions/) — they hold
live session cookies and never reach the repo tree.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

_LOGIN_URL_RX = re.compile(r"(signin|servicelogin|/login|session/new|relogin|sso/login)", re.I)


def _config_root() -> Path:
    try:
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        return Path(WORKSPACE_DIR)
    except Exception:  # loaded outside the suijin process
        return Path.home() / ".suijin" / "config"


def sessions_dir() -> Path:
    d = _config_root() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_path(target: str, name: str = "") -> Path:
    slug = re.sub(r"[^a-z0-9.-]+", "-", str(target).lower().strip())
    return sessions_dir() / (f"{slug}--{name}.json" if name else f"{slug}.json")


def _default_cookie_domain(target: str) -> str:
    """Best-effort registrable domain for the pasted jar.

    accounts.google.com → .google.com (auth cookies ride the parent).
    The --domain flag overrides for hosts the heuristic gets wrong
    (ccTLDs, multi-label TLDs like .co.uk).
    """
    host = urlparse("//" + str(target).strip()).hostname or str(target).strip()
    host = host[4:] if host.startswith("www.") else host
    labels = host.split(".")
    if len(labels) > 2:
        return "." + ".".join(labels[-2:])
    return "." + host


def cookie_string_to_state(cookie_str: str, target: str, domain: str = "") -> dict:
    """Cookie string → playwright storage_state (prefix-safe).

    Handles the real-world paste: optional 'Cookie:' header prefix,
    whitespace, and __Secure-/__Host- prefixes (secure flag + url-form
    for __Host-, which must not carry a domain).
    """
    s = str(cookie_str or "").strip()
    if s.lower().startswith("cookie:"):
        s = s[7:].strip()
    dom = domain or _default_cookie_domain(target)
    host = (urlparse("//" + str(target).strip()).hostname or str(target).strip())
    cookies = []
    for pair in s.split(";"):
        name, _, value = pair.strip().partition("=")
        if not name or "=" not in pair:
            continue
        if name.startswith("__Host-"):
            cookies.append(
                {"name": name, "value": value, "url": f"https://{host}/", "secure": True}
            )
        else:
            c = {"name": name, "value": value, "domain": dom, "path": "/"}
            if name.startswith("__Secure-"):
                c["secure"] = True
            cookies.append(c)
    return {"cookies": cookies, "origins": []}


def state_to_cookie_string(state: dict) -> str:
    """storage_state → cookie header string (for the HTTP credential)."""
    return "; ".join(f"{c['name']}={c['value']}" for c in state.get("cookies", []))


def save_session(target: str, state: dict, name: str = "") -> Path:
    p = session_path(target, name)
    p.write_text(json.dumps(state, indent=2))
    return p


def load_session(target: str, name: str = "") -> dict | None:
    p = session_path(target, name)
    if not p.exists():
        return None
    with contextlib.suppress(Exception):
        return json.loads(p.read_text())
    return None


def register_session_credential(target: str, state: dict, name: str = "") -> str:
    """Make the session visible to http_request/http_replay in EVERY
    process (the shared file store — see the bridge tests for the
    two-stores bug this respects)."""
    jar = state_to_cookie_string(state)
    if not jar:
        return ""
    from suijin.modules.credential_store.main import creds_add

    creds_add(service=(name or target), cred_type="cookie", value=jar)
    return name or target


def browser_login_capture(target: str, name: str = "") -> dict:
    """Visible blank-slate Chromium; the operator logs in by hand and
    presses Enter in the terminal. Returns the captured storage_state.

    Needs a display — this is the one flow that cannot run headless
    (that's the point: the human does 2FA/SSO/captcha in a real
    browser; the password never touches suijin).
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()  # blank slate — no prior state
        page = ctx.new_page()
        with contextlib.suppress(Exception):
            page.goto(f"https://{target}", timeout=20000)
        print("\n  browser is open — navigate and log in (2FA/SSO are fine).")
        print("  when you're logged in, come back here and press Enter…")
        try:
            input()
        except EOFError:
            pass
        state = ctx.storage_state()
        ctx.close()
        browser.close()
    return state


def check_session(target: str, name: str = "", check_url: str = "") -> str:
    """Heartbeat: is the session still alive?

    Heuristic, honestly labeled: GET the target (or check_url) with the
    jar; dead if we land on a login page or get 401/403. A 200 that
    isn't a login page = alive. Target-specific verdicts beat guessing.
    """
    state = load_session(target, name)
    if not state:
        return f"no session on file for '{target}'" + (f" (name: {name})" if name else "")
    jar = {c["name"]: c["value"] for c in state.get("cookies", [])}
    url = check_url or f"https://{(urlparse('//'+target).hostname or target)}/"
    try:
        import requests

        sess = requests.Session()
        sess.max_redirects = 10
        r = sess.get(url, cookies=jar, allow_redirects=True, timeout=20,
                     headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    except requests.exceptions.TooManyRedirects:
        return "INCONCLUSIVE — redirect loop on the check URL (use --check-url to pin a stable page)"
    except Exception as e:  # noqa: BLE001
        return f"check failed (network): {e}"
    final = str(r.url)
    if _LOGIN_URL_RX.search(final):
        return f"EXPIRED — redirected to a login page ({final[:90]})"
    if r.status_code in (401, 403):
        return f"EXPIRED — {r.status_code} with session attached"
    return f"alive — {r.status_code}, final: {final[:90]} ({len(jar)} cookies)"


_AUTH_COOKIE_RX = re.compile(r"(^|_)(sid|sess|session|token|auth|login|sso|jwt|lsid|osid|sapisid)", re.I)


def _registrable(host: str) -> str:
    host = (urlparse("//" + str(host)).hostname or str(host)).lower()
    host = host[4:] if host.startswith("www.") else host
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) > 2 else host


def snapshot_jars(ctx, last: dict) -> list[tuple[str, int, bool]]:
    """Read every cookie in the browser, group by registrable domain,
    save + register the jars that changed. Returns [(domain, n_cookies,
    registered)]. Live-refresh safe: register_or_replace semantics keep
    creds_get serving the CURRENT jar after re-logins."""
    out = []
    with contextlib.suppress(Exception):
        cookies = ctx.cookies()  # playwright format == storage_state format
        by_dom: dict[str, list] = {}
        for c in cookies:
            dom = _registrable(c.get("domain") or "")
            if dom and "." in dom:
                by_dom.setdefault(dom, []).append(c)
        for dom, jar in by_dom.items():
            sig = sorted(f"{c['name']}={c['value']}" for c in jar)
            key = hash(tuple(sig))
            if last.get(dom) == key:
                continue
            last[dom] = key
            state = {"cookies": jar, "origins": []}
            save_session(dom, state)
            authish = len(jar) >= 4 or any(_AUTH_COOKIE_RX.search(c["name"]) for c in jar)
            if authish:
                from suijin.modules.credential_store.main import creds_add_replace

                creds_add_replace(service=dom, cred_type="cookie",
                                  value=state_to_cookie_string(state))
            out.append((dom, len(jar), authish))
    return out


def _stealth_playwright():
    """Prefer patchright (protocol-level stealth) over stock playwright.

    Google-class IdPs reject sign-ins from automation-flagged browsers:
    stock playwright leaks CDP signals (Runtime.enable, webdriver flag,
    --enable-automation). patchright is a drop-in fork that patches the
    protocol itself — same API, mac + linux, pip-only. Capability
    check: absent → stock playwright, and paste mode is always the
    universal fallback. Returns (sync_playwright, stack_name)."""
    with contextlib.suppress(Exception):
        from patchright.sync_api import sync_playwright as _pw

        return _pw, "patchright"
    from playwright.sync_api import sync_playwright as _pw

    return _pw, "playwright"


def _launch_browser_context(p, profile: Path, headless: bool = False, channels=("chrome", None)):
    """Launch a login context the TARGET will trust, or the best
    available fallback. Capability CHECK, not platform guess (the
    cross-environment rule): 'chrome' resolves per-OS (app bundle on
    macOS, PATH binary on Linux); Google-class IdPs reject sign-ins
    from automation-flagged Chromium builds ('This browser or app may
    not be secure'). Automation signals are stripped in BOTH branches.
    Real Chrome absent (containers, boxes) → bundled Chromium; no
    browser at all → the caller points at paste mode. Sessions,
    credentials, and paste mode never depend on this choice.

    Returns (context, engine_name)."""
    common = dict(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    last_err: Exception | None = None
    for channel in channels:
        if channel is None:
            try:
                return p.chromium.launch_persistent_context(str(profile), **common), "chromium"
            except Exception as e:  # noqa: BLE001
                last_err = e
        else:
            try:
                return p.chromium.launch_persistent_context(str(profile), channel=channel, **common), channel
            except Exception as e:  # noqa: BLE001 — probe next engine
                last_err = e
    raise last_err or RuntimeError("no browser engine available")


def live_session_browser(start_url: str = "", poll_seconds: float = 4.0) -> int:
    """`suijin auth` (bare) — THE live cookie jar.

    Opens a persistent-profile browser (the profile lives in
    ~/.suijin/config/auth-profile, so logins survive between runs).
    Prefers the operator's REAL Chrome when present — some providers
    (Google among them) block sign-in from automation Chromium builds;
    on hosts with no Chrome, degrades to bundled Chromium and says so.
    Blank slate by default — navigate wherever you like; --start or the
    auth_start_url config key sets a landing page (nothing hardcoded:
    the first field run shipped with the then-current target baked in).
    Log in to as many things as you want — every poll, changed jars are
    snapshotted per-domain and auth-looking ones are (re)registered as
    credentials, LIVE: agents' next http_request(credential='<domain>')
    picks up the fresh jar with no restart. Works mid-engagement.

    Ctrl+C (or closing the browser) stops it; sessions stay saved.
    """
    sync_playwright, stack = _stealth_playwright()

    profile = _config_root() / "auth-profile"
    profile.mkdir(parents=True, exist_ok=True)
    start = str(start_url or "").strip()
    if not start:
        with contextlib.suppress(Exception):
            from suijin.modules.platform.lib.config_loader import load_config

            start = str(load_config().get("auth_start_url") or "").strip()
    print("  opening browser — log in to whatever you like, as much as you like.")
    print(f"  profile persists at {profile} (re-open anytime; logins survive)")
    print("  jars snapshot automatically; agents pick them up live. Ctrl+C to stop.\n")
    last: dict = {}
    announced: set[str] = set()
    try:
        with sync_playwright() as p:
            try:
                ctx, engine = _launch_browser_context(p, profile)
            except Exception as e:  # noqa: BLE001 — headless box, no engine
                print(f"  cannot open a browser on this host ({e.__class__.__name__}).")
                print("  use paste mode instead: suijin auth <target> --paste")
                return 1
            note = (
                "your real Chrome — providers trust it for sign-ins"
                if engine == "chrome"
                else "bundled Chromium — some providers (e.g. Google) may block sign-in;"
                " if that happens use: suijin auth <target> --paste"
            )
            print(f"  engine: {engine} via {stack} ({note})", flush=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if start:
                with contextlib.suppress(Exception):
                    page.goto(start, timeout=15000)
            try:
                while True:
                    time.sleep(poll_seconds)
                    for dom, n, authish in snapshot_jars(ctx, last):
                        tag = "credential registered" if authish else "session saved"
                        fresh = "" if dom in announced else "  NEW "
                        announced.add(dom)
                        print(f"  [{tag:>20}] {fresh}{dom} ({n} cookies)", flush=True)
            except KeyboardInterrupt:
                pass
            with contextlib.suppress(Exception):
                snapshot_jars(ctx, last)  # final flush
                ctx.close()
    except KeyboardInterrupt:
        pass
    except Exception as e:  # noqa: BLE001 — browser closed by operator etc.
        print(f"  browser closed ({e.__class__.__name__})")
    print(f"\n  {len(announced)} domain jar(s) captured — suijin auth --list")
    return 0


def run_auth_cmd(args) -> int:
    """Entry behind `suijin auth`."""
    target = str(getattr(args, "target", "") or "").strip()
    name = str(getattr(args, "name", "") or "").strip()
    domain = str(getattr(args, "domain", "") or "").strip()
    mode_paste = bool(getattr(args, "paste", False))
    mode_browser = bool(getattr(args, "browser", False))
    do_check = bool(getattr(args, "check", False))
    do_list = bool(getattr(args, "list", False))  # explicit ONLY — bare
    # `suijin auth` must reach the live browser, not fall into the list
    # branch (the bug the operator hit two minutes after shipping)

    if do_list:
        files = sorted(sessions_dir().glob("*.json"))
        if not files:
            print("no sessions on file — suijin auth (live browser) | <target> --paste")
            return 0
        print("sessions:")
        for f in files:
            age_h = (time.time() - f.stat().st_mtime) / 3600
            state = json.loads(f.read_text()) if f.suffix == ".json" else {}
            n = len(state.get("cookies", []))
            print(f"  {f.stem:40s} {n:3d} cookies  saved {age_h:.1f}h ago")
        return 0

    if not target and not mode_paste and not mode_browser and not do_check:
        # bare `suijin auth` — the live cookie jar (sessions persist in
        # the profile; cookies stored locally, shared across engagements)
        return live_session_browser()

    if not target:
        print("usage: suijin auth <target> --paste | --browser | --check")
        print("  (or bare `suijin auth` for the live cookie jar browser)")
        return 1

    if do_check:
        print(check_session(target, name, str(getattr(args, "check_url", "") or "")))
        return 0

    if not mode_paste and not mode_browser:
        print(f"usage: suijin auth {target} --paste | --browser | --check [--name id] [--domain .root.tld]")
        print("  --paste   read a cookie string from stdin (devtools → copy cookie header)")
        print("  --browser open a visible browser; you log in, press Enter, session captured")
        print("  --check   heartbeat: is the saved session still alive?")
        return 1

    if mode_browser:
        state = browser_login_capture(target, name)
    else:
        import sys

        raw = sys.stdin.read().strip() or (sys.stdin.readline().strip() if not sys.stdin.isatty() else "")
        if not raw:
            # interactive fallback
            print("paste the cookie string (end with an empty line):")
            lines = []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if not line.strip():
                    break
                lines.append(line.strip())
            raw = " ".join(lines)
        state = cookie_string_to_state(raw, target, domain)

    if not state.get("cookies"):
        print("no cookies captured — nothing saved")
        return 1

    p = save_session(target, state, name)
    cred = register_session_credential(target, state, name)
    print(f"  ✓ session saved: {p} ({len(state['cookies'])} cookies)")
    print(f"  ✓ credential '{cred}' registered — every process sees it")
    print(f"    agents attach it: http_request(..., credential='{cred}')")
    print(f"    browser contexts are born with the jar: storage_state={p}")
    print(f"  heartbeat: suijin auth {target} --check")
    return 0
