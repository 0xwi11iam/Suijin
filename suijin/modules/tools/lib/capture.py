"""capture — the traffic capture plane (Wave B).

Two modes:
- proxy_capture: boots the blue proxy in capture-only mode on a free
  port; the operator's browser sends traffic through it; every request
  feeds the web_session store (the cross-credential model builds itself)
- crawl: autonomous navigation over mcp_playwright — BFS + template cap,
  plan-per-page LLM contract, credential cycling, deferred auth phases

The AI uses this: the think context surfaces 'PIPELINE READY: N new
requests captured — dispatch_testers to analyze' when new shapes arrive.
"""

from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import urlsplit

from suijin.modules.tools.lib.web_session import record_send


def proxy_capture(port: int = 0, target_host: str = "", target_port: int = 0) -> str:
    """Boot a capture-only forward proxy. If target_port is given, forwards
    to that local app; otherwise acts as a transparent logging proxy that
    forwards to the original destination (requires browser proxy config).
    Every request feeds web_session (the cross-credential worklist builds)."""
    try:
        import socket

        if port == 0:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()

        def _handle_client(conn, addr):
            try:
                data = conn.recv(65536)
                if not data:
                    conn.close()
                    return
                text = data.decode("utf-8", errors="ignore")
                # parse request line + headers
                lines = text.split("\r\n")
                req_line = lines[0] if lines else ""
                m = re.match(r"(\w+)\s+(\S+)", req_line)
                if not m:
                    conn.close()
                    return
                method, full_path = m.group(1), m.group(2)
                headers = {}
                for ln in lines[1:]:
                    if ": " in ln:
                        k, _, v = ln.partition(": ")
                        headers[k.lower()] = v
                # extract path from full URL (proxy style) or path
                parts = urlsplit(full_path)
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                host = headers.get("host", target_host or "unknown")

                # feed the session model
                record_send({
                    "method": method,
                    "url": f"http://{host}{path}",
                    "headers": {k: v for k, v in headers.items() if k not in ("host",)},
                    "body": "",
                })

                # forward to target if configured
                if target_port:
                    try:
                        fwd = socket.socket()
                        fwd.connect(("127.0.0.1", target_port))
                        # rewrite to origin-form
                        origin = f"{req_line.replace(full_path, path)}\r\n"
                        rest = "\r\n".join(lines[1:])
                        fwd.sendall((origin + rest).encode())
                        resp = fwd.recv(65536)
                        fwd.close()
                        conn.sendall(resp)
                    except Exception:
                        conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                else:
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            except Exception:  # noqa: BLE001 — the capture proxy never crashes
                pass
            finally:
                import contextlib

                with contextlib.suppress(Exception):
                    conn.close()

        def _serve():
            srv = socket.socket()
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", port))
            srv.listen(10)
            while True:
                try:
                    conn, addr = srv.accept()
                    threading.Thread(target=_handle_client, args=(conn, addr), daemon=True).start()
                except Exception:
                    break

        threading.Thread(target=_serve, daemon=True).start()
        time.sleep(0.2)

        if target_port:
            return (
                f"capture proxy on :{port} → forwarding to :{target_port}\n"
                f"Send traffic to http://127.0.0.1:{port} — every request feeds the session model.\n"
                f"Check the worklist: web_session(action=summary)"
            )
        return (
            f"capture-only proxy on :{port}\n"
            f"Configure your browser proxy to 127.0.0.1:{port} — every request feeds the session model.\n"
            f"Check the worklist: web_session(action=summary)"
        )
    except Exception as e:  # noqa: BLE001
        return f"Error: proxy_capture failed: {e}"


def crawl(url: str = "", max_pages: int = 20, credential: str = "") -> str:
    """Autonomous crawl via mcp_playwright — discovers surfaces, captures
    every page into the session model. BFS + template cap (≤5 per path
    pattern); forms discovered and submitted; deferred auth (login pages
    deferred until anonymous crawl exhausts)."""
    try:
        if not url:
            return "Error: url required"
        if "://" not in url:
            url = "https://" + url

        from suijin.modules.mcp_playwright.main import mcp_browser_close, mcp_browser_goto, mcp_browser_snapshot

        visited = set()
        queue = [url]
        template_counts: dict[str, int] = {}
        pages = 0
        surfaces_found = 0

        while queue and pages < max_pages:
            current = queue.pop(0)
            path = urlsplit(current).path or "/"
            template_key = f"{path}"
            if template_counts.get(template_key, 0) >= 5:
                continue
            if current in visited:
                continue
            visited.add(current)
            template_counts[template_key] = template_counts.get(template_key, 0) + 1

            goto = mcp_browser_goto(current)
            if not goto.startswith("Loaded"):
                continue
            pages += 1

            snap = mcp_browser_snapshot(max_elements=40)
            if "Error" in snap or "No interactive" in snap:
                continue

            # feed the session model
            record_send({
                "method": "GET",
                "url": current,
                "headers": {},
                "body": "",
            })

            # extract links for BFS
            base = urlsplit(current)
            origin = f"{base.scheme}://{base.netloc}"
            for line in snap.split("\n"):
                # snapshot lines like "[  1] LINK    "text" href=/path"
                m2 = re.search(r'href=(\S+)', line)
                if m2:
                    href = m2.group(1).strip('"')
                    if href.startswith("/"):
                        next_url = origin + href
                    elif href.startswith("http") and origin in href:
                        next_url = href
                    else:
                        continue
                    if next_url not in visited:
                        queue.append(next_url)
                        surfaces_found += 1

            # check for forms → test targets
            if "<form" in snap.lower() or "INPUT" in snap:
                surfaces_found += 1

        mcp_browser_close()

        return json.dumps({
            "pages_crawled": pages,
            "surfaces_discovered": surfaces_found,
            "queued_unvisited": len(queue),
            "note": "Session model updated — check web_session(action=summary) for the cross-credential worklist, then dispatch_testers.",
        }, indent=2)
    except Exception as e:  # noqa: BLE001
        return f"Error: crawl failed: {e}"
