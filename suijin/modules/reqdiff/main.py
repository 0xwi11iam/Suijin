import requests

_T = (5, 20)
_UA = {"User-Agent": "Mozilla/5.0 (suijin recon)"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


def _fetch(u):
    r = _get(u.strip())
    return r.status_code, dict(r.headers), r.text


def response_diff(url_a: str = "", url_b: str = "") -> str:
    if not url_a or not url_b:
        return "Error: two urls required"
    try:
        sa, ha, ba = _fetch(url_a)
        sb, hb, bb = _fetch(url_b)
    except requests.RequestException as e:
        return f"Error: {e}"
    import difflib

    out = [f"A: {sa} {len(ba):,}B | B: {sb} {len(bb):,}B"]
    hdr_delta = {k: (ha.get(k), hb.get(k)) for k in set(ha) | set(hb) if ha.get(k) != hb.get(k)}
    if hdr_delta:
        out.append(
            "header deltas: "
            + "; ".join(f"{k}: {v[0] or '-'} vs {v[1] or '-'}" for k, v in list(hdr_delta.items())[:8])
        )
    diff = [
        d
        for d in difflib.unified_diff(ba.splitlines(), bb.splitlines(), lineterm="", n=0)
        if d[:1] in "+-" and d[1:2] not in ("+", "-")
    ][:30]
    if diff:
        out.append("body diff:\n  " + "\n  ".join(diff))
    return "\n".join(out)


def header_diff(url_a: str = "", url_b: str = "") -> str:
    if not url_a or not url_b:
        return "Error: two urls required"
    try:
        sa, ha, _ = _fetch(url_a)
        sb, hb, _ = _fetch(url_b)
    except requests.RequestException as e:
        return f"Error: {e}"
    keys = sorted(set(ha) | set(hb))
    rows = [f"{k:28} A={ha.get(k, '-')} | B={hb.get(k, '-')}" for k in keys if ha.get(k) != hb.get(k)]
    return f"A: {sa}, B: {sb}\n" + ("\n".join(rows[:25]) or "identical headers")
