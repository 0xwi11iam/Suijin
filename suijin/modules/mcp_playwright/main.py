"""MCP Playwright — headless browser automation with DOM snapshots.

Uses Playwright sync API in a dedicated background thread with its own
event loop to avoid the sync/async conflict with the main asyncio loop.
All tool functions are sync (called via route_tool) and communicate with
the browser thread via thread-safe queues.

Field-hardened (spa-target.example run):
- playwright NOT installed -> instant actionable message (the old code
  hung 30s and said 'Browser timeout' for a missing dependency)
- wait_until default is domcontentloaded — networkidle NEVER fires on SPAs
  with analytics/websockets (amplitude etc), guaranteeing 30s timeouts
- generation counter: a late result from a dead/timeout generation can
  never be served to the next call (stale-result corruption)
- queues drained on (re)start; real stealth UA instead of a truncated
  scanner-tell string
"""

import json, os, re, tempfile, threading, queue, time
from pathlib import Path

_cmd_queue = queue.Queue()
_result_queue = queue.Queue()
_browser_ready = threading.Event()
_browser_thread = None
_generation = 0  # bumped on every (re)start; stale results are discarded
PLAYWRIGHT_MISSING = (
    "playwright is not installed. Install it, then fetch the browser:\n"
    "  pip install playwright\n"
    "  playwright install chromium\n"
    "Then retry this tool."
)


def _browser_loop():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _browser_ready.set()
        return
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
    from suijin.modules.platform.lib.stealth import browser_identity

    ident = browser_identity()
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800}, user_agent=ident.get("User-Agent", "Mozilla/5.0")
    )
    page = ctx.new_page()
    _browser_ready.set()
    try:
        while True:
            item = _cmd_queue.get()
            if item is None:
                break
            gen, cmd, kwargs = item
            try:
                result = _dispatch(page, cmd, kwargs)
                _result_queue.put((gen, "ok", result))
            except Exception as e:
                _result_queue.put((gen, "err", str(e)))
    finally:
        try:
            ctx.close()
        except:
            pass
        try:
            browser.close()
        except:
            pass
        try:
            pw.stop()
        except:
            pass


def _start():
    """Ensure the browser thread is alive and the dependency is present.
    Returns an error string immediately when playwright is missing."""
    global _browser_thread, _generation
    try:
        import playwright  # noqa: F401
    except ImportError:
        return PLAYWRIGHT_MISSING
    if _browser_thread and _browser_thread.is_alive():
        return None
    # fresh generation: drain stale queues so a late result from the dead
    # thread can never be served to a new call
    _generation += 1
    for q in (_cmd_queue, _result_queue):
        with q.mutex:
            q.queue.clear()
    _browser_ready.clear()
    _browser_thread = threading.Thread(target=_browser_loop, daemon=True)
    _browser_thread.start()
    if not _browser_ready.wait(timeout=30):
        return "Browser startup timed out (chromium may need `playwright install chromium`)"
    return None


def _call(cmd, **kw):
    err = _start()
    if err:
        return err
    gen = _generation
    _cmd_queue.put((gen, cmd, kw))
    deadline = time.monotonic() + 90  # comfortably above the 20s goto cap
    while time.monotonic() < deadline:
        try:
            rgen, status, result = _result_queue.get(timeout=max(0.1, deadline - time.monotonic()))
        except queue.Empty:
            return "Browser timeout (90s) — the page may be hung; retry or use http_request"
        if rgen != gen:
            continue  # stale result from a previous generation — discard
        return str(result) if status == "ok" else f"Browser error: {result}"
    return "Browser timeout (90s)"


def _dispatch(page, cmd, kw):
    if cmd == "goto":
        # domcontentloaded: networkidle never fires on analytics-heavy SPAs
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
    return f"Unknown: {cmd}"


def _snap(page, max_el):
    global _snapshot_elements
    els = page.evaluate("""() => {
        const sel = 'a,button,input,select,textarea,[role="button"],[role="link"],[onclick]';
        const r = []; const seen = new Set();
        document.querySelectorAll(sel).forEach(el => {
            if (r.length >= 200) return;
            const rect = el.getBoundingClientRect();
            if (!rect.width || !rect.height || rect.bottom < 0 || rect.top > innerHeight) return;
            const t = el.tagName.toLowerCase(), id = el.id || '';
            const cls = (el.className && typeof el.className === 'string') ? el.className.split(' ').slice(0,2).join('.') : '';
            const txt = (el.textContent||el.getAttribute('aria-label')||el.getAttribute('placeholder')||'').trim().slice(0,60);
            const href = el.getAttribute('href')||'', nm = el.getAttribute('name')||'', tp = el.getAttribute('type')||'';
            let s = id ? '#'+id : cls ? t+'.'+cls.replace(/\\s+/g,'.') : nm ? t+'[name="'+nm+'"]' : href&&t==='a' ? 'a[href="'+href.slice(0,40)+'"]' : txt ? t+':has-text("'+txt.slice(0,30)+'")' : '';
            const k = s||txt; if (seen.has(k)) return; seen.add(k);
            r.push({idx:r.length+1,tag:t,text:txt,sel:s,type:tp||'',href:href.slice(0,80),x:Math.round(rect.x+rect.width/2),y:Math.round(rect.y+rect.height/2)});
        });
        return r;
    }""")
    if not els:
        return "No interactive elements."
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
                    page.mouse.click(e["x"], e["y"])
            except:
                page.mouse.click(e["x"], e["y"])
            page.wait_for_timeout(500)
            return f'Clicked [{i + 1}] {e["tag"]} "{e["text"][:40]}"'
    try:
        page.click(str(sel), timeout=10000)
    except:
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
    except:
        page.click(str(sel), timeout=5000)
        page.keyboard.type(str(text), delay=50)
    return f"Typed '{text[:40]}' into {sel}"


# Public tool functions (sync — called via route_tool)
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
    _generation += 1  # invalidate any in-flight results
    try:
        _cmd_queue.put(None)
    except:
        pass
    _browser_thread = None
    return "Browser closed."
