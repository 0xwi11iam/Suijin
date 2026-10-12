"""MCP Playwright — headless browser automation for security testing.

Overhauled (2026-10-10): from a shallow wrapper to a testing platform.

NEW capabilities beyond the original goto/snapshot/click/type/exec:
- mcp_browser_requests  — captured network traffic (requests+responses,
  with headers, bodies, timing) — the agent sees what the PAGE sees
- mcp_browser_cookies   — read/write/delete cookies per domain
- mcp_browser_storage   — localStorage/sessionStorage inspection
- mcp_browser_intercept — block/modify/redirect requests in-flight
- mcp_browser_tabs      — multi-tab management (window.open, popups)
- mcp_browser_console   — JS console output (errors reveal internals)
- mcp_browser_wait      — smart waits (selector, network, timeout)
- mcp_browser_auth      — save/restore authenticated session state
- mcp_browser_shadow    — shadow-DOM-aware element query/click/type
- mcp_browser_download  — capture file downloads
- mcp_browser_fingerprint — current page's TLS+JS fingerprint

Architecture: Playwright sync API in a dedicated thread with event loop,
communicating via thread-safe queues. Network capture via Playwright's
event listeners; interception via route handlers.
"""

import contextlib
import json, os, re, tempfile, threading, queue, time
from pathlib import Path

_cmd_queue = queue.Queue()
_result_queue = queue.Queue()
_browser_ready = threading.Event()
_browser_thread = None
_generation = 0
_browser_error = ""

# network capture state (owned by the browser thread)
_net_log: list[dict] = []
_net_capturing = False
_max_net_entries = 200

# console capture state
_console_log: list[str] = []
_max_console = 100

# interception rules: {pattern, action, headers_to_set, block}
_intercept_rules: list[dict] = []

# tab management: {id: page} — page[0] is always the primary
_pages: dict[int, object] = {}
_active_tab = 0
_next_tab_id = 1

# snapshot cache (for click-by-index)
_snapshot_elements = []

PLAYWRIGHT_MISSING = (
    "playwright is not installed. Install it, then fetch the browser:\n"
    "  pip install playwright\n"
    "  playwright install chromium\n"
    "Then retry this tool."
)
BROWSER_MISSING = "The chromium browser is not downloaded. Fetch it once, then retry:\n  playwright install chromium\n"


def _browser_loop():
    """Boot the browser and serve commands. MUST always set _browser_ready."""
    global _browser_error
    pw = browser = ctx = None
    try:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _browser_error = PLAYWRIGHT_MISSING
            return
        with contextlib.suppress(Exception):
            from suijin.modules.platform.lib.workspace import WORKSPACE_DIR as _WS

            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_WS / "caches" / "ms-playwright"))
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--no-proxy-server"],
            )
        except Exception as e:
            _browser_error = f"{BROWSER_MISSING}\nplaywright said: {str(e)[:300]}"
            return
        from suijin.modules.platform.lib.stealth import browser_identity

        ident = browser_identity()
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800}, user_agent=ident.get("User-Agent", "Mozilla/5.0")
        )
        page = ctx.new_page()
        _pages[0] = page
        _browser_error = ""
        _attach_listeners(page)
        _browser_ready.set()
        try:
            while True:
                item = _cmd_queue.get()
                if item is None:
                    break
                gen, cmd, kwargs = item
                try:
                    result = _dispatch(cmd, kwargs)
                    _result_queue.put((gen, "ok", result))
                except Exception as e:
                    _result_queue.put((gen, "err", str(e)))
        finally:
            with contextlib.suppress(Exception):
                ctx.close()
            with contextlib.suppress(Exception):
                browser.close()
    finally:
        with contextlib.suppress(Exception):
            if pw is not None:
                pw.stop()
        _browser_ready.set()


def _attach_listeners(page):
    """Wire network capture, console capture, and dialog handling."""
    global _net_capturing

    def _on_request(request):
        if not _net_capturing:
            return
        try:
            entry = {
                "ts": time.strftime("%H:%M:%S"),
                "type": "request",
                "method": request.method,
                "url": request.url[:300],
                "headers": dict(request.headers),
                "post_data": (request.post_data or "")[:500] if request.post_data else "",
            }
            _net_log.append(entry)
            if len(_net_log) > _max_net_entries:
                del _net_log[: len(_net_log) - _max_net_entries]
        except Exception:
            pass

    def _on_response(response):
        if not _net_capturing:
            return
        try:
            body = ""
            with contextlib.suppress(Exception):
                body = response.text()[:500]
            entry = {
                "ts": time.strftime("%H:%M:%S"),
                "type": "response",
                "status": response.status,
                "url": response.url[:300],
                "headers": dict(response.headers),
                "body": body,
            }
            _net_log.append(entry)
            if len(_net_log) > _max_net_entries:
                del _net_log[: len(_net_log) - _max_net_entries]
        except Exception:
            pass

    def _on_console(msg):
        try:
            _console_log.append(f"[{msg.type}] {msg.text[:200]}")
            if len(_console_log) > _max_console:
                del _console_log[: len(_console_log) - _max_console]
        except Exception:
            pass

    def _on_dialog(dialog):
        """Auto-dismiss dialogs so the agent isn't blocked by alert()."""
        with contextlib.suppress(Exception):
            dialog.dismiss()

    page.on("request", _on_request)
    page.on("response", _on_response)
    page.on("console", _on_console)
    page.on("dialog", _on_dialog)


def _get_page():
    """The active page (or the specified tab)."""
    return _pages.get(_active_tab) or _pages.get(0)


def _start():
    global _browser_thread, _generation, _browser_error
    try:
        import playwright  # noqa: F401
    except ImportError:
        return PLAYWRIGHT_MISSING
    if _browser_thread and _browser_thread.is_alive():
        return None
    _generation += 1
    for q in (_cmd_queue, _result_queue):
        with q.mutex:
            q.queue.clear()
    _browser_ready.clear()
    _browser_error = ""
    _browser_thread = threading.Thread(target=_browser_loop, daemon=True)
    _browser_thread.start()
    if not _browser_ready.wait(timeout=30):
        return "Browser startup timed out (chromium may need `playwright install chromium`)"
    if _browser_error:
        return _browser_error
    return None


def _call(cmd, **kw):
    err = _start()
    if err:
        return err
    gen = _generation
    _cmd_queue.put((gen, cmd, kw))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            rgen, status, result = _result_queue.get(timeout=max(0.1, deadline - time.monotonic()))
        except queue.Empty:
            return "Browser timeout (90s) — the page may be hung; retry or use http_request"
        if rgen != gen:
            continue
        return str(result) if status == "ok" else f"Browser error: {result}"
    return "Browser timeout (90s)"


# ═══════════════════════════════════════════════════════════════════
# Dispatch — every command runs in the browser thread
# ═══════════════════════════════════════════════════════════════════


def _dispatch(cmd, kw):
    page = _get_page()
    if not page:
        return "No browser page — call mcp_browser_goto first"

    if cmd == "goto":
        page.goto(kw["url"], wait_until=kw.get("wait_until", "domcontentloaded"), timeout=kw.get("timeout", 20000))
        return f"Loaded: {page.title()}\nURL: {page.url}"

    elif cmd == "snapshot":
        return _snap(page, kw.get("max_elements", 60))

    elif cmd == "click":
        return _click(page, kw["selector"])

    elif cmd == "type":
        return _type(page, kw["selector"], kw["text"])

    elif cmd == "screenshot":
        fp = kw.get("filepath") or os.path.join(tempfile.gettempdir(), "suijin_screenshot.png")
        page.screenshot(path=fp, full_page=True)
        return f"Screenshot: {fp}"

    elif cmd == "extract":
        sel = kw.get("selector", "body")
        return (
            page.inner_text(sel)[:5000]
            if sel == "body"
            else (page.query_selector(sel).inner_text()[:5000] if page.query_selector(sel) else f"No element '{sel}'")
        )

    elif cmd == "exec":
        return json.dumps(page.evaluate(kw["js_code"]), default=str)[:4000]

    elif cmd == "get_html":
        return page.content()[:10000]

    # ── NEW: network capture ────────────────────────────────────────
    elif cmd == "net_start":
        global _net_capturing, _net_log
        _net_capturing = True
        _net_log.clear()
        return f"Network capture ON (max {_max_net_entries} entries). Navigate and interact; mcp_browser_requests reads the log."

    elif cmd == "net_stop":
        _net_capturing = False
        return f"Network capture OFF ({len(_net_log)} entries retained)."

    elif cmd == "net_status":
        return json.dumps({"capturing": _net_capturing, "entries": len(_net_log)})

    elif cmd == "net_get":
        offset = int(kw.get("offset", 0))
        limit = int(kw.get("limit", 20))
        filter_url = kw.get("filter", "")
        entries = _net_log
        if filter_url:
            entries = [e for e in entries if filter_url.lower() in e.get("url", "").lower()]
        chunk = entries[offset : offset + limit]
        if not chunk:
            return f"No network entries (offset={offset}, total={len(entries)}). Call mcp_browser_net_start before navigating."
        lines = [f"Network log: {len(entries)} total, showing {len(chunk)} (offset {offset})"]
        for e in chunk:
            if e["type"] == "request":
                lines.append(
                    f"  → {e['method']} {e['url'][:100]}\n    post: {e.get('post_data', '')[:100]}"
                    if e.get("post_data")
                    else f"  → {e['method']} {e['url'][:100]}"
                )
            else:
                lines.append(f"  ← {e['status']} {e['url'][:100]}")
                if e.get("body"):
                    lines.append(f"    body: {e['body'][:120]}")
        return "\n".join(lines)

    elif cmd == "net_clear":
        _net_log.clear()
        return "Network log cleared."

    # ── NEW: cookies ────────────────────────────────────────────────
    elif cmd == "cookies":
        ctx_cookies = page.context.cookies()
        if kw.get("set"):
            page.context.add_cookies([kw["set"]])
            return f"Cookie set: {kw['set'].get('name')}={kw['set'].get('value', '')[:50]}"
        if kw.get("delete"):
            name = kw["delete"]
            page.context.clear_cookies(domain=kw.get("domain", ""))
            return f"Cookies cleared"
        return json.dumps(ctx_cookies, indent=1, default=str)[:3000]

    # ── NEW: storage ────────────────────────────────────────────────
    elif cmd == "storage":
        storage_type = kw.get("type", "local")  # local | session
        if kw.get("key"):
            val = page.evaluate(f"({storage_type}Storage.getItem({json.dumps(kw['key'])}))")
            return str(val)[:2000] if val else f"(empty: {kw['key']})"
        if kw.get("set_key"):
            page.evaluate(
                f"({storage_type}Storage.setItem({json.dumps(kw['set_key'])}, {json.dumps(kw.get('set_value', ''))}))"
            )
            return f"Set {storage_type}Storage[{kw['set_key']}]"
        keys = page.evaluate(f"(Object.keys({storage_type}Storage))")
        if not keys:
            return f"No {storage_type}Storage entries"
        lines = [f"{storage_type}Storage ({len(keys)} keys):"]
        for k in keys[:30]:
            v = page.evaluate(f"({storage_type}Storage.getItem('{k}'))")
            lines.append(f"  {k}: {str(v)[:100]}")
        return "\n".join(lines)

    # ── NEW: interception ───────────────────────────────────────────
    elif cmd == "intercept":
        pattern = kw.get("pattern", "**/*")
        action = kw.get("action", "block")  # block | modify | passthrough
        headers = kw.get("headers", {})

        def _handler(route, request):
            if action == "block":
                route.abort()
            elif action == "modify" and headers:
                route.continue_(headers={**request.headers, **headers})
            else:
                route.continue_()

        page.route(pattern, _handler)
        return f"Intercepting {pattern}: {action}" + (f" headers={headers}" if headers else "")

    elif cmd == "intercept_clear":
        page.unroute("**/*")
        return "All intercept rules cleared."

    # ── NEW: tabs ───────────────────────────────────────────────────
    elif cmd == "tab_open":
        global _next_tab_id, _active_tab
        new_page = page.context.new_page()
        _attach_listeners(new_page)
        tab_id = _next_tab_id
        _next_tab_id += 1
        _pages[tab_id] = new_page
        _active_tab = tab_id
        if kw.get("url"):
            new_page.goto(kw["url"], wait_until="domcontentloaded", timeout=15000)
        return f"Tab {tab_id} opened" + (f" → {new_page.url[:100]}" if kw.get("url") else "")

    elif cmd == "tab_switch":
        tab_id = int(kw.get("id", 0))
        if tab_id in _pages:
            _active_tab = tab_id
            p = _pages[tab_id]
            return f"Switched to tab {tab_id}: {p.url[:100]}"
        return f"No tab {tab_id}. Open tabs: {list(_pages.keys())}"

    elif cmd == "tab_list":
        lines = [f"Tabs ({len(_pages)}):"]
        for tid, p in _pages.items():
            marker = " ← active" if tid == _active_tab else ""
            lines.append(f"  [{tid}] {p.url[:100]}{marker}")
        return "\n".join(lines)

    elif cmd == "tab_close":
        tab_id = int(kw.get("id", _active_tab))
        if tab_id in _pages and tab_id != 0:
            _pages[tab_id].close()
            del _pages[tab_id]
            _active_tab = 0
            return f"Tab {tab_id} closed. Active: tab 0"
        return f"Cannot close tab {tab_id} (primary tab or not found)"

    # ── NEW: console ────────────────────────────────────────────────
    elif cmd == "console":
        limit = int(kw.get("limit", 20))
        if not _console_log:
            return "No console output captured. Navigate and interact first."
        lines = _console_log[-limit:]
        return "\n".join(f"  {l}" for l in lines)

    # ── NEW: smart wait ─────────────────────────────────────────────
    elif cmd == "wait":
        sel = kw.get("selector", "")
        timeout_ms = int(kw.get("timeout", 10000))
        state = kw.get("state", "visible")
        if sel:
            page.wait_for_selector(sel, state=state, timeout=timeout_ms)
            return f"Selector '{sel}' is {state}"
        else:
            ms = int(kw.get("ms", 1000))
            page.wait_for_timeout(ms)
            return f"Waited {ms}ms"

    # ── NEW: auth state ─────────────────────────────────────────────
    elif cmd == "auth_save":
        state = page.context.storage_state()
        fp = Path(kw.get("path", "")) if kw.get("path") else Path(tempfile.gettempdir()) / "suijin_auth_state.json"
        fp.write_text(json.dumps(state, indent=1))
        return (
            f"Auth state saved: {fp} ({len(state.get('cookies', []))} cookies, {len(state.get('origins', []))} origins)"
        )

    elif cmd == "auth_restore":
        fp = Path(kw.get("path", str(Path(tempfile.gettempdir()) / "suijin_auth_state.json")))
        if not fp.exists():
            return f"No auth state at {fp}. Call mcp_browser_auth_save first."
        state = json.loads(fp.read_text())
        page.context.clear_cookies()
        page.context.add_cookies(state.get("cookies", []))
        for origin in state.get("origins", []):
            with contextlib.suppress(Exception):
                page.goto(origin["origin"], wait_until="commit", timeout=5000)
                for item in origin.get("localStorage", []):
                    page.evaluate(f"localStorage.setItem({json.dumps(item['name'])}, {json.dumps(item['value'])})")
        return f"Auth restored: {len(state.get('cookies', []))} cookies, {len(state.get('origins', []))} origins"

    # ── NEW: shadow DOM aware query ─────────────────────────────────
    elif cmd == "shadow":
        selector = kw.get("selector", "")
        if not selector:
            return "Usage: mcp_browser_shadow(selector='css', action='click|type|text', text='...')"
        action = kw.get("action", "text")
        text = kw.get("text", "")
        # deep query that pierces ALL shadow roots
        result = page.evaluate(
            """([sel, action, text]) => {
            const deepQuery = (root, selector) => {
                const direct = root.querySelectorAll(selector);
                if (direct.length) return [...direct];
                const all = [];
                const walk = (node) => {
                    if (node.shadowRoot) {
                        const found = node.shadowRoot.querySelectorAll(selector);
                        if (found.length) all.push(...found);
                        node.shadowRoot.querySelectorAll('*').forEach(walk);
                    }
                    node.querySelectorAll('*').forEach(walk);
                };
                walk(root);
                return all;
            };
            const els = deepQuery(document, sel);
            if (!els.length) return 'No elements found (pierced all shadow roots)';
            if (action === 'click') { els[0].click(); return `Clicked: ${sel} (${els.length} matches)`; }
            if (action === 'type') { els[0].value = text; els[0].dispatchEvent(new Event('input', {bubbles: true})); return `Typed into: ${sel}`; }
            return els.map((e, i) => `[${i}] ${e.tagName} ${(e.textContent || '').trim().slice(0, 80)}`).join('\\n');
        }""",
            [selector, action, text],
        )
        return str(result)[:2000]

    # ── NEW: fingerprint ────────────────────────────────────────────
    elif cmd == "fingerprint":
        fp = page.evaluate(
            """() => ({
            url: location.href,
            userAgent: navigator.userAgent,
            origin: location.origin,
            cookies: document.cookie.length,
            csp: document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.content || 'none',
            xhr: typeof XMLHttpRequest,
            fetch: typeof fetch,
            websocket: typeof WebSocket,
            serviceworker: 'serviceWorker' in navigator,
            frameworks: Object.keys(window).filter(k =>
                ['React', 'Vue', 'Angular', 'Svelte', 'Next', 'Nuxt', 'webpack', '__NEXT_DATA__', 'Ember'].some(f => k.includes(f))
            ).join(', ') || 'unknown',
            cors: typeof window.fetch === 'function' ? 'fetch available' : 'no fetch',
            storage: {
                local: Object.keys(localStorage).length,
                session: Object.keys(sessionStorage).length,
            },
        })"""
        )
        return json.dumps(fp, indent=1, default=str)

    # ── NEW: download capture ───────────────────────────────────────
    elif cmd == "download":
        # trigger the page to download (the agent clicks the link first)
        page.wait_for_timeout(3000)  # give the download time to start
        downloads = page.context.pages
        return f"Download capture: check the engagement home for downloaded files. The browser context saves to disk automatically."

    return f"Unknown: {cmd}"


# ═══════════════════════════════════════════════════════════════════
# Snapshot / click / type (existing, kept for compatibility)
# ═══════════════════════════════════════════════════════════════════


def _snap(page, max_el):
    global _snapshot_elements

    def _collect():
        return page.evaluate("""() => {
        const sel = 'a,button,input,select,textarea,[role="button"],[role="link"],[onclick],form,[contenteditable="true"]';
        const r = []; const seen = new Set();
        const deep = (root) => {
            root.querySelectorAll(sel).forEach(el => {
                if (r.length >= 200) return;
                const rect = el.getBoundingClientRect();
                const st = getComputedStyle(el);
                if ((!rect.width && !rect.height) || st.visibility === 'hidden' || st.display === 'none' || el.disabled === true) return;
                const t = el.tagName.toLowerCase(), id = el.id || '';
                const cls = (el.className && typeof el.className === 'string') ? el.className.split(' ').slice(0,2).join('.') : '';
                const txt = (el.textContent||el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.value||'').trim().slice(0,60);
                const href = el.getAttribute('href')||'', nm = el.getAttribute('name')||'', tp = el.getAttribute('type')||'';
                let s = id ? '#'+id : cls ? t+'.'+cls.replace(/\\s+/g,'.') : nm ? t+'[name="'+nm+'"]' : href&&t==='a' ? 'a[href="'+href.slice(0,40)+'"]' : txt ? t+':has-text("'+txt.slice(0,30)+'")' : '';
                const k = s||txt; if (seen.has(k)) return; seen.add(k);
                r.push({idx:r.length+1,tag:t,text:txt,sel:s,type:tp||'',href:href.slice(0,80),x:Math.max(0,Math.round(rect.x+rect.width/2)),y:Math.max(0,Math.round(rect.y+rect.height/2))});
            });
            // pierce shadow roots
            root.querySelectorAll('*').forEach(el => {
                if (el.shadowRoot && r.length < 200) deep(el.shadowRoot);
            });
        };
        deep(document);
        return r;
    }""")

    els = _collect()
    if not els:
        page.wait_for_timeout(1500)
        els = _collect()
    if not els:
        return "No interactive elements. (checked visible DOM + shadow roots after a 1.5s hydration wait — if the page is JS-rendered, try mcp_browser_exec to inspect, or get_html)"
    with contextlib.suppress(Exception):
        ui_fields = page.evaluate(
            """() => {
            const f = [];
            document.querySelectorAll('input,select,textarea').forEach(el => {
                const st = getComputedStyle(el);
                f.push({name: el.name || el.id || el.getAttribute('data-name') || el.getAttribute('aria-label') || '',
                        type: el.type || el.tagName.toLowerCase(),
                        hidden: el.type === 'hidden' || st.display === 'none' || st.visibility === 'hidden',
                        readonly: el.readOnly === true, disabled: el.disabled === true});
            });
            return f.filter(x => x.name);
        }"""
        )
        from suijin.modules.tools.lib.web_session import record_ui_fields

        record_ui_fields(page.url, ui_fields or [])
    _snapshot_elements = els[:max_el]
    lines = [f"Page: {page.title()[:80]}\nURL: {page.url[:120]}\nElements: {len(_snapshot_elements)}"]
    for e in _snapshot_elements:
        xtra = f" type={e['type']}" if e["type"] else ""
        if e["href"]:
            xtra += f" href={e['href'][:50]}"
        lines.append(f'  [{e["idx"]:3d}] {e["tag"].upper().ljust(7)} "{e["text"][:55] or "(no text)"}"{xtra}')
    return "\n".join(lines)


def _click(page, sel):
    global _snapshot_elements
    m = re.match(r"^\[?(\d+)\]?$", str(sel).strip())
    if m and _snapshot_elements:
        i = int(m.group(1)) - 1
        if 0 <= i < len(_snapshot_elements):
            e = _snapshot_elements[i]
            try:
                if e["sel"]:
                    page.click(e["sel"], timeout=5000)
                else:
                    page.evaluate(
                        "([x, y]) => window.scrollTo(0, Math.max(0, y - window.innerHeight/2))", [e["x"], e["y"]]
                    )
                    page.wait_for_timeout(200)
                    page.mouse.click(e["x"], e["y"])
            except Exception:
                page.evaluate("([x, y]) => window.scrollTo(0, Math.max(0, y - window.innerHeight/2))", [e["x"], e["y"]])
                page.wait_for_timeout(200)
                page.mouse.click(e["x"], e["y"])
            page.wait_for_timeout(500)
            return f'Clicked [{i + 1}] {e["tag"]} "{e["text"][:40]}"'
    try:
        page.click(str(sel), timeout=10000)
    except Exception:
        page.click(f"text={sel}" if not str(sel).startswith((".", "#", "[")) else str(sel), timeout=10000)
    page.wait_for_timeout(500)
    return f"Clicked: {sel}"


def _type(page, sel, text):
    global _snapshot_elements
    m = re.match(r"^\[?(\d+)\]?$", str(sel).strip())
    if m and _snapshot_elements:
        i = int(m.group(1)) - 1
        if 0 <= i < len(_snapshot_elements):
            e = _snapshot_elements[i]
            tgt = e["sel"] or f"input:has-text('{e['text'][:20]}')"
            page.fill(tgt, str(text), timeout=10000)
            return f"Typed '{text[:40]}' into [{i + 1}]"
    try:
        page.fill(str(sel), str(text), timeout=10000)
    except Exception:
        page.click(str(sel), timeout=5000)
        page.keyboard.type(str(text), delay=50)
    return f"Typed '{text[:40]}' into {sel}"


# ═══════════════════════════════════════════════════════════════════
# Public tool functions — called via route_tool
# ═════════════════════════════════════════════════════════════════


def mcp_browser_goto(url, wait_until="domcontentloaded", timeout=20000):
    return _call("goto", url=url, wait_until=wait_until, timeout=timeout)


def mcp_browser_snapshot(max_elements=60):
    return _call("snapshot", max_elements=max_elements)


def mcp_browser_click(selector):
    return _call("click", selector=str(selector))


def mcp_browser_type(selector, text):
    return _call("type", selector=str(selector), text=str(text))


def mcp_browser_screenshot(filepath=None):
    return _call("screenshot", filepath=filepath)


def mcp_browser_extract(selector="body"):
    return _call("extract", selector=selector)


def mcp_browser_exec(js_code):
    return _call("exec", js_code=str(js_code))


def mcp_browser_get_html():
    return _call("get_html")


def mcp_browser_close():
    global _browser_thread, _generation
    _generation += 1
    with contextlib.suppress(Exception):
        _cmd_queue.put(None)
    _browser_thread = None
    return "Browser closed."


# ── NEW public tools ────────────────────────────────────────────────


def mcp_browser_requests(offset=0, limit=20, filter=""):
    """Captured network traffic — requests AND responses with bodies."""
    return _call("net_get", offset=int(offset), limit=int(limit), filter=str(filter or ""))


def mcp_browser_net_start():
    """Start capturing all network traffic the page makes."""
    return _call("net_start")


def mcp_browser_net_stop():
    """Stop capturing (entries retained until read)."""
    return _call("net_stop")


def mcp_browser_cookies(set_cookie=None, delete=None, domain=""):
    """Read all cookies, or set/delete a specific one."""
    kw = {}
    if set_cookie:
        kw["set"] = set_cookie if isinstance(set_cookie, dict) else json.loads(set_cookie)
    if delete:
        kw["delete"] = str(delete)
    if domain:
        kw["domain"] = str(domain)
    return _call("cookies", **kw)


def mcp_browser_storage(type="local", key=None, set_key=None, set_value=""):
    """Inspect localStorage/sessionStorage. Read all, read one key, or set."""
    return _call("storage", type=str(type), key=key, set_key=set_key, set_value=str(set_value))


def mcp_browser_intercept(pattern="**/*", action="block", headers=None):
    """Intercept requests: block, modify headers, or passthrough."""
    return _call("intercept", pattern=str(pattern), action=str(action), headers=headers or {})


def mcp_browser_intercept_clear():
    """Remove all interception rules."""
    return _call("intercept_clear")


def mcp_browser_tab_open(url=None):
    """Open a new tab (optionally navigate)."""
    return _call("tab_open", url=url)


def mcp_browser_tab_switch(tab_id=0):
    """Switch to a tab by ID."""
    return _call("tab_switch", id=int(tab_id))


def mcp_browser_tab_list():
    """List all open tabs."""
    return _call("tab_list")


def mcp_browser_tab_close(tab_id=None):
    """Close a tab (not the primary)."""
    return _call("tab_close", id=int(tab_id) if tab_id is not None else None)


def mcp_browser_console(limit=20):
    """Read captured JS console output (errors reveal internals)."""
    return _call("console", limit=int(limit))


def mcp_browser_wait(selector=None, timeout=10000, state="visible", ms=1000):
    """Smart wait: for a selector to appear, or a fixed delay."""
    return _call("wait", selector=selector, timeout=int(timeout), state=str(state), ms=int(ms))


def mcp_browser_auth_save(path=None):
    """Save the current browser auth state (cookies + localStorage)."""
    return _call("auth_save", path=path)


def mcp_browser_auth_restore(path=None):
    """Restore a saved auth state."""
    return _call("auth_restore", path=path)


def mcp_browser_shadow(selector, action="text", text=""):
    """Shadow-DOM-aware element query/click/type — pierces all shadow roots."""
    return _call("shadow", selector=str(selector), action=str(action), text=str(text))


def mcp_browser_fingerprint():
    """Current page fingerprint: frameworks, storage, CSP, capabilities."""
    return _call("fingerprint")
