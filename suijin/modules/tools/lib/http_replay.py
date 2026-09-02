"""http_replay — the governed replay engine: payloads travel as DATA.

The sanctioned send path for testing (Wave 1 of the Web Evidence
Engine). A stored or inline request is mutated through 15 structured
ops + 11 composable codecs, sent with byte-exact control, and returned
with structured evidence — compare mode bundles the 3-gate protocol
(baseline / exploit / diff) into ONE call.

Design contract (learned from the field review + the CyberStrike study):
- values stay RAW through mutation — nothing silently re-encodes; the
  agent controls exactly what bytes land on the wire
- every send: scope-checked, paced, budgeted (module-level 5,000-request
  session cap + AIMD limiter — per-call budgets are their bug, not ours)
- every result: a copy-pasteable curl equivalent (self-documenting PoCs)
  + DBMS/stacktrace error signatures
- credential swap strips the 7 common auth headers and injects a named
  credential — the IDOR/vertical-authz primitive
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

# ── state ────────────────────────────────────────────────────────────
_BUDGET = {"remaining": 5000}  # module-level: survives across calls
_LIMITER = {"rps": 5.0, "lock": threading.Lock()}
_LAST_SEND = {"t": 0.0, "lock": threading.Lock()}
_TRAFFIC_LOCK = threading.Lock()
_CREDENTIALS: dict[str, dict] = {}  # name -> {headers: {...}, cookies: "..."}

COMMON_AUTH_HEADERS = (
    "authorization",
    "cookie",
    "x-auth-token",
    "x-api-key",
    "x-access-token",
    "x-session-token",
    "x-csrf-token",
)

_ERROR_SIGS = [
    ("sqli_mysql", re.compile(r"you have an error in your sql syntax|mysql_fetch|MariaDB", re.I)),
    ("sqli_mssql", re.compile(r"Microsoft SQL| unclosed quotation|SqlClient", re.I)),
    ("sqli_oracle", re.compile(r"\bORA-\d{5}|Oracle error", re.I)),
    ("sqli_postgres", re.compile(r"PostgreSQL.*ERROR|pg_query", re.I)),
    ("sqli_sqlite", re.compile(r"SQLite3?::|sqlite_error|unrecognized token", re.I)),
    ("sqli_odbc", re.compile(r"Microsoft OLE DB|ODBC Driver", re.I)),
    ("stacktrace", re.compile(r"Traceback \(most recent call last\)|at [\w.$]+\([\w.java]+:\d+\)", re.I)),
]


def _traffic_path() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR, engagement_dir

    base = engagement_dir() or (WORKSPACE_DIR / "outputs" / "engagements" / "_default")
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    return base / "traffic.jsonl"


def _store_request(req: dict) -> str:
    rid = f"r{int(time.time() * 1000) % 10**10}"
    entry = {"id": rid, **req, "ts": time.time()}
    try:
        with _TRAFFIC_LOCK, open(_traffic_path(), "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:  # noqa: BLE001 — capture must never break a send
        pass
    return rid


def _load_request(request_id: str) -> dict | None:
    try:
        with open(_traffic_path()) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if e.get("id") == request_id:
                    return {k: e.get(k) for k in ("method", "url", "headers", "body", "cookies")}
    except Exception:  # noqa: BLE001
        pass
    return None


def _record_replay(req: dict, note: str = "") -> None:
    """Capture a replay send into the same store (marked)."""
    try:
        with _TRAFFIC_LOCK, open(_traffic_path(), "a") as f:
            f.write(
                json.dumps(
                    {
                        "id": f"s{int(time.time() * 1000) % 10**10}",
                        "replay": True,
                        "note": note[:60],
                        **req,
                        "ts": time.time(),
                    },
                    default=str,
                )
                + "\n"
            )
    except Exception:  # noqa: BLE001
        pass


# ── codecs (11, composable left-to-right) ───────────────────────────
def _codec_url(v, all_bytes=False):
    from urllib.parse import quote

    return quote(str(v), safe="" if all_bytes else "")


def apply_codec(value: str, pipeline: list[str]) -> str:
    out = str(value)
    for c in pipeline or []:
        c = str(c).strip().lower()
        if c == "url":
            out = _codec_url(out)
        elif c == "url-all":
            out = _codec_url(out, all_bytes=True)
        elif c == "url-double":
            out = _codec_url(_codec_url(out))
        elif c == "base64":
            out = base64.b64encode(out.encode()).decode()
        elif c == "base64url":
            out = base64.urlsafe_b64encode(out.encode()).decode().rstrip("=")
        elif c == "hex":
            out = out.encode().hex()
        elif c == "html-dec":
            out = "".join(f"&#{ord(ch)};" for ch in out)
        elif c == "html-hex":
            out = "".join(f"&#x{ord(ch):x};" for ch in out)
        elif c == "unicode":
            out = "".join(ch if ord(ch) < 128 else f"\\u{ord(ch):04x}" for ch in out)
        elif c == "tab":  # WAF evasion: full url-encode but %09 as the space escape
            from urllib.parse import quote

            out = quote(out, safe="").replace("%20", "%09")
        elif c == "upper":
            out = out.upper()
        elif c == "lower":
            out = out.lower()
        else:
            raise ValueError(f"unknown codec '{c}'")
    return out


# ── mutations (15 ops, values RAW) ───────────────────────────────────
def _set_query(url: str, name: str, value: str) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != name]
    q.append((name, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _add_query(url: str, name: str, value: str) -> str:
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True) + [(name, value)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _remove_query(url: str, name: str) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != name]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _body_json(body: str) -> dict:
    try:
        return json.loads(body)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"body is not JSON: {e}") from e


def apply_mutation(req: dict, op: str, field: str = "", value=None) -> dict:
    """One structured op on a request dict. Values stay RAW."""
    r = dict(req)
    headers = dict(r.get("headers") or {})
    op = str(op).strip().lower()
    if op == "set-query":
        r["url"] = _set_query(r["url"], field, str(value))
    elif op == "add-query":  # HPP: ?id=1&id=2
        r["url"] = _add_query(r["url"], field, str(value))
    elif op == "remove-query":
        r["url"] = _remove_query(r["url"], field)
    elif op == "set-header":
        headers[field] = str(value)
    elif op == "add-header":  # append-style headers (cookies-style lists; true duplicates via raw mode)
        existing = headers.get(field)
        headers[field] = f"{existing}, {value}" if existing is not None else str(value)
    elif op == "remove-header":
        headers.pop(field, None)
    elif op == "set-body":
        r["body"] = str(value)
    elif op == "set-method":
        r["method"] = str(value).upper()
    elif op == "set-target":
        parts = urlsplit(r["url"])
        r["url"] = urlunsplit((parts.scheme, parts.netloc, str(value), parts.query, parts.fragment))
    elif op == "set-path-param":  # replaces {field} in the path
        parts = urlsplit(r["url"])
        r["url"] = urlunsplit(
            (parts.scheme, parts.netloc, parts.path.replace("{" + field + "}", str(value)), parts.query, parts.fragment)
        )
    elif op == "body-merge":
        d = _body_json(r.get("body") or "{}")
        if not isinstance(value, dict):
            raise ValueError("body-merge value must be an object")
        d.update(value)
        r["body"] = json.dumps(d)
    elif op == "body-set-field":
        d = _body_json(r.get("body") or "{}")
        cur = d
        parts = str(field).split(".")
        for p in parts[:-1]:
            if not isinstance(cur.get(p), dict):
                cur[p] = {}
            cur = cur[p]
        try:
            cur[parts[-1]] = json.loads(str(value))
        except Exception:  # noqa: BLE001 — non-JSON values stay strings
            cur[parts[-1]] = str(value)
        r["body"] = json.dumps(d)
    elif op == "body-remove-field":
        d = _body_json(r.get("body") or "{}")
        cur = d
        parts = str(field).split(".")
        for p in parts[:-1]:
            cur = cur.get(p) if isinstance(cur, dict) else None
            if cur is None:
                break
        if isinstance(cur, dict):
            cur.pop(parts[-1], None)
        r["body"] = json.dumps(d)
    elif op == "set-cookie":
        r["cookies"] = str(value)
    elif op == "remove-cookie":
        r["cookies"] = ""
    else:
        raise ValueError(f"unknown mutation op '{op}'")
    r["headers"] = headers
    return r


def apply_credential(req: dict, cred_name: str | None) -> dict:
    """Strip ALL common auth headers (+cookie header), inject the named
    credential's. cred_name=None → unauthenticated. THE access-control
    primitive: one argument, byte-exact."""
    r = dict(req)
    headers = {k: v for k, v in dict(r.get("headers") or {}).items() if k.lower() not in COMMON_AUTH_HEADERS}
    if cred_name:
        cred = _CREDENTIALS.get(str(cred_name))
        if cred is None:
            raise ValueError(
                f"unknown credential '{cred_name}' — register_credential first; known: {sorted(_CREDENTIALS)}"
            )
        headers.update(dict(cred.get("headers") or {}))
        if cred.get("cookies"):
            headers["Cookie"] = cred["cookies"]
    r["headers"] = headers
    return r


# ── send (governed) ──────────────────────────────────────────────────
def _paced_send():
    with _LAST_SEND["lock"]:
        gap = 1.0 / max(0.5, _LIMITER["rps"])
        wait = _LAST_SEND["t"] + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST_SEND["t"] = time.monotonic()


def _aimd(success: bool, throttled: bool):
    with _LIMITER["lock"]:
        if throttled:
            _LIMITER["rps"] = max(0.5, _LIMITER["rps"] / 2)
        elif success:
            _LIMITER["rps"] = min(20.0, _LIMITER["rps"] + 1)


def _scope_guard(url: str, base_url: str | None) -> str | None:
    """Replay targets must be same-origin as the base request, or on the
    explicit allowlist (SSRF-style internal targets need allow_internal)."""
    try:
        host = urlsplit(url).netloc.lower()
        if base_url and host == urlsplit(base_url).netloc.lower():
            return None
        if host.startswith(("127.", "localhost", "0.0.0.0", "[::1]", "10.", "192.168.", "169.254.")) or re.match(
            r"^172\.(1[6-9]|2\d|3[01])\.", host
        ):
            return (
                f"refused: private/loopback target '{host}' without allow_internal=true "
                "(SSRF testing: pass allow_internal=true deliberately)"
            )
        return None
    except Exception:  # noqa: BLE001
        return "refused: unparseable url"


def _curl_of(req: dict) -> str:
    parts = [f"curl -X {req.get('method', 'GET')} '{req.get('url')}'"]
    for k, v in dict(req.get("headers") or {}).items():
        parts.append(f"  -H '{k}: {v}'")
    if req.get("cookies"):
        parts.append("  -H 'Cookie: " + str(req.get("cookies")) + "'")
    if req.get("body"):
        parts.append(f"  --data-raw '{str(req.get('body'))[:400]}'")
    return " \\\n".join(parts)


def _send(req: dict, timeout: int = 30, follow_redirects: bool = False) -> dict:
    if _BUDGET["remaining"] <= 0:
        return {"error": "session request budget exhausted (5000) — anti-self-DoS backstop"}
    _BUDGET["remaining"] -= 1
    headers = dict(req.get("headers") or {})
    if req.get("cookies"):
        headers["Cookie"] = req["cookies"]
    _paced_send()
    try:
        resp = requests.request(
            req.get("method", "GET"),
            req["url"],
            headers=headers,
            data=req.get("body") or None,
            timeout=(10, timeout),
            verify=False,
            allow_redirects=follow_redirects,
        )
        throttled = resp.status_code in (429, 503)
        _aimd(success=not throttled, throttled=throttled)
        out = {
            "status": resp.status_code,
            "length": len(resp.text or ""),
            "ms": int(resp.elapsed.total_seconds() * 1000),
            "location": resp.headers.get("Location", ""),
            "set_cookie": resp.headers.get("Set-Cookie", ""),
            "body": (resp.text or "")[:1500],
            "error_signatures": [name for name, rx in _ERROR_SIGS if rx.search(resp.text or "")],
        }
        return out
    except requests.RequestException as e:
        _aimd(success=False, throttled="timeout" in str(e).lower())
        return {"error": f"transport: {e}"}


# ── diff (Gate 3) ────────────────────────────────────────────────────
def _diff(a: dict, b: dict) -> dict:
    return {
        "status_match": a.get("status") == b.get("status"),
        "length_match": a.get("length") == b.get("length"),
        "content_match": (a.get("body") or "") == (b.get("body") or ""),
        "timing_delta_ms": abs(int(a.get("ms") or 0) - int(b.get("ms") or 0)),
        "verdict_hint": (
            "NO measurable difference — per the 3-gate protocol this is NOT a finding"
            if a.get("status") == b.get("status") and a.get("body") == b.get("body")
            else "measurable difference — attach this diff as finding evidence"
        ),
    }


# ── the tool entry points ────────────────────────────────────────────
def http_replay(
    request_id: str = "",
    method: str = "GET",
    url: str = "",
    headers: dict | None = None,
    body: str = "",
    mutations: list | None = None,
    codec: list | None = None,
    codec_field: str = "",
    credential: str = "",
    unauthenticated: bool = False,
    compare: dict | None = None,
    sweep: dict | None = None,
    follow_redirects: bool = False,
    timeout: int = 30,
    allow_internal: bool = False,
) -> str:
    """The governed replay engine. Payloads as data, evidence per call.

    - inline request (method/url/headers/body) OR stored request_id
    - mutations: [{op, field, value}] — 15 ops, values RAW
    - codec: [names] applied to codec_field's value (or the body) — 11 composable
    - credential: swap auth to a named credential (IDOR/vertical-authz primitive)
    - unauthenticated: strip all auth headers
    - compare: {"mutations": [...], "credential": "..."} → baseline vs exploit
      + structured diff (the 3-gate protocol in ONE call)
    - sweep: {"op": "set-query", "field": "id", "values": [...]} — ≤50 paced values
    """
    try:
        base = None
        if request_id:
            base = _load_request(str(request_id))
            if base is None:
                return f"Error: request_id '{request_id}' not found in the traffic store"
        if base is None:
            if not url:
                return "Error: provide url (+method/headers/body) or a stored request_id"
            base = {
                "method": str(method).upper(),
                "url": str(url),
                "headers": dict(headers or {}),
                "body": str(body or ""),
                "cookies": "",
            }

        if not allow_internal:
            guard = _scope_guard(base["url"], base_url=None)
            if guard:
                return f"Error: {guard}"

        if compare is not None:
            exploit_req = dict(base)
            for m in compare.get("mutations") or []:
                exploit_req = apply_mutation(exploit_req, m.get("op", ""), m.get("field", ""), m.get("value"))
            if compare.get("credential") or compare.get("unauthenticated"):
                exploit_req = apply_credential(exploit_req, compare.get("credential"))
            a = _send(base, timeout, follow_redirects)
            b = _send(exploit_req, timeout, follow_redirects)
            _record_replay(exploit_req, "compare-exploit")
            return json.dumps(
                {
                    "mode": "compare",
                    "baseline": {k: a.get(k) for k in ("status", "length", "ms", "location", "set_cookie", "body")},
                    "exploit": {k: b.get(k) for k in ("status", "length", "ms", "location", "set_cookie", "body")},
                    "error_signatures": b.get("error_signatures") or [],
                    "diff": _diff(a, b),
                    "curl": _curl_of(exploit_req),
                },
                indent=2,
            )[:6000]

        if sweep is not None:
            values = [str(v) for v in (sweep.get("values") or [])][:50]
            if not values:
                return "Error: sweep needs values (≤50)"
            out = []
            for v in values:
                r = apply_mutation(dict(base), sweep.get("op", "set-query"), sweep.get("field", ""), v)
                res = _send(r, timeout, follow_redirects)
                out.append(
                    {
                        "value": v[:60],
                        "status": res.get("status", "?"),
                        "len": res.get("length", 0),
                        "ms": res.get("ms", 0),
                    }
                )
            _record_replay(base, f"sweep:{sweep.get('field', '')}")
            [o for o in out if o["status"] not in (out[0]["status"],) if out] or out
            return json.dumps(
                {
                    "mode": "sweep",
                    "field": sweep.get("field"),
                    "results": out,
                    "status_changes": len([o for o in out if o["status"] != out[0]["status"]]) if out else 0,
                },
                indent=2,
            )[:6000]

        # single mode
        r = dict(base)
        for m in mutations or []:
            r = apply_mutation(r, m.get("op", ""), m.get("field", ""), m.get("value"))
        if codec:
            target_field = codec_field
            pipeline = [str(c) for c in codec]
            if target_field:
                # the codec OUTPUT is the intended wire form — it must land
                # VERBATIM (urlencode re-encoding %09 -> %2509 was the bug)
                from urllib.parse import quote_plus

                parts = urlsplit(r["url"])
                q = parse_qsl(parts.query, keep_blank_values=True)
                segs = [
                    f"{quote_plus(k)}={apply_codec(v, pipeline)}" if k == target_field
                    else f"{quote_plus(k)}={quote_plus(v)}"
                    for k, v in q
                ]
                r["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(segs), parts.fragment))
            else:
                r["body"] = apply_codec(str(r.get("body") or ""), pipeline)
        if credential or unauthenticated:
            r = apply_credential(r, credential or None)
        guard = _scope_guard(r["url"], base_url=base["url"] if not allow_internal else None)
        if guard and not allow_internal:
            return f"Error: {guard}"
        res = _send(r, timeout, follow_redirects)
        _record_replay(r, "single")
        return json.dumps({"mode": "single", **res, "curl": _curl_of(r)}, indent=2)[:6000]
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001 — tools return strings, never raise
        return f"Error: http_replay failed: {e}"


def http_replay_raw(host: str = "", port: int = 443, tls: bool = True, data: str = "", timeout: int = 15) -> str:
    """Raw TCP/TLS send — smuggling, desync, duplicate headers, Host
    overrides. Bytes go on the wire VERBATIM; the response returns raw."""
    import socket
    import ssl

    try:
        if _BUDGET["remaining"] <= 0:
            return "Error: session request budget exhausted"
        _BUDGET["remaining"] -= 1
        raw = str(data).encode("latin-1", "replace")
        sock = socket.create_connection((str(host), int(port)), timeout=timeout)
        if tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=str(host))
        try:
            sock.sendall(raw)
            sock.settimeout(timeout)
            chunks = []
            total = 0
            while total < 2_000_000:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            resp = b"".join(chunks)
        finally:
            sock.close()
        _record_replay(
            {"method": "RAW", "url": f"{host}:{port}", "headers": {}, "body": raw[:200].decode("latin-1", "replace")},
            "raw",
        )
        return json.dumps(
            {"mode": "raw", "bytes": len(resp), "response_head": resp[:1200].decode("latin-1", "replace")}, indent=2
        )[:4000]
    except Exception as e:  # noqa: BLE001
        return f"Error: raw send failed: {e}"


def register_credential(name: str = "", headers: dict | None = None, cookies: str = "") -> str:
    """Register a named credential (auth headers + cookie string) for
    credential-swap replay. Capture them from a login you already hold."""
    try:
        if not str(name).strip():
            return "Error: name required"
        _CREDENTIALS[str(name)] = {"headers": dict(headers or {}), "cookies": str(cookies or "")}
        return (
            f"Credential '{name}' registered ({len(_CREDENTIALS)} total). Swap with http_replay(credential='{name}')."
        )
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def list_credentials() -> str:
    if not _CREDENTIALS:
        return "No credentials registered. register_credential(name, headers, cookies) — capture from a login you hold."
    return json.dumps(
        {
            k: {"header_keys": sorted((v.get("headers") or {}).keys()), "cookies": bool(v.get("cookies"))}
            for k, v in _CREDENTIALS.items()
        },
        indent=2,
    )
