"""URL hygiene pipeline (2026-10-20, hexstrike adaptation).

Recon floods blow context windows: a crawl returns 800 URLs that are
really 12 templates with different ids. Three cheap, deterministic
tools keep the flood out of the model's eye — the `uro`/`qsreplace`/
`anew` trio as pure Python, no binaries:

- url_dedup   collapse same-template URLs (scheme+host+path+param NAMES,
              numeric/id VALUES collapsed) — keep first K per template
- url_rewrite qsreplace — set every query param value to one value
- url_merge   anew — append only lines a file doesn't already hold
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

_VALUE_IS_ID = re.compile(r"^\d+$|^[0-9a-f]{8,}$|^[A-Za-z0-9_-]{16,}$")


def _template_of(url: str) -> tuple:
    """The URL's template key: numeric/long-opaque values collapsed."""
    try:
        parts = urlsplit(str(url).strip())
        query = sorted(parse_qsl(parts.query, keep_blank_values=True))
        norm_q = tuple(
            (k, ("<id>" if (_VALUE_IS_ID.match(v or "") or (k.lower() in _ID_PARAM_NAMES and v)) else v))
            for k, v in query
        )
        path = re.sub(r"/\d+(?=/|$)", "/<id>", parts.path)
        return (parts.scheme.lower(), parts.netloc.lower(), path, norm_q)
    except Exception:  # noqa: BLE001 — malformed URLs collapse to themselves
        return (str(url),)


_ID_PARAM_NAMES = {
    "id",
    "uid",
    "user_id",
    "userid",
    "account_id",
    "order_id",
    "orderid",
    "post_id",
    "page_id",
    "item_id",
    "product_id",
    "object_id",
    "doc_id",
    "ref",
}


def url_dedup(urls: list, keep_per_template: int = 5) -> str:
    """Collapse same-template URLs; keep the first K examples of each
    template. 800 crawl URLs become ~12 template groups."""
    try:
        groups: dict[tuple, list[str]] = {}
        order: list[tuple] = []
        for u in urls or []:
            if not str(u or "").strip():
                continue
            key = _template_of(str(u))
            if key not in groups:
                groups[key] = []
                order.append(key)
            if len(groups[key]) < max(1, int(keep_per_template or 5)):
                groups[key].append(str(u).strip())
        lines = []
        for key in order:
            members = groups[key]
            scheme, netloc, path, _q = key
            label = f"{scheme}://{netloc}{path}" if scheme else str(key)[:60]
            lines.append(f"{label}  ({len(members)} kept)")
            lines.extend(f"  {m}" for m in members)
        total_in = len([u for u in (urls or []) if str(u or "").strip()])
        head = f"DEDUP: {total_in} urls -> {len(order)} templates (max {keep_per_template} kept each)"
        return head + "\n" + "\n".join(lines) if lines else head
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def url_rewrite(urls: list, value: str = "FUZZ") -> str:
    """qsreplace — set EVERY query param's value to `value` (default
    FUZZ), returning the rewritten URLs. The fuzzing-ready form."""
    try:
        out = []
        for u in urls or []:
            u = str(u or "").strip()
            if not u:
                continue
            try:
                parts = urlsplit(u)
                pairs = parse_qsl(parts.query, keep_blank_values=True)
                if not pairs:
                    continue  # nothing to rewrite
                q = "&".join(f"{k}={value}" for k, _ in pairs)
                out.append(urlunsplit((parts.scheme, parts.netloc, parts.path, q, parts.fragment)))
            except Exception:  # noqa: BLE001
                continue
        return "\n".join(out) if out else "No urls with query params to rewrite."
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def url_merge(path: str, urls: list) -> str:
    """anew — append only the lines the file doesn't already hold
    (append-only dedupe; prints what was added)."""
    try:
        p = Path(str(path or "").strip())
        if not p.is_absolute():
            from suijin.modules.platform.lib.workspace import home_dir

            p = Path(home_dir()) / p
        existing: set[str] = set()
        if p.exists():
            existing = {ln.strip() for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()}
        added = []
        for u in urls or []:
            s = str(u or "").strip()
            if s and s not in existing:
                existing.add(s)
                added.append(s)
        if added:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(added) + "\n")
        return f"merged: {len(added)} added, {len(existing) - len(added)} already present -> {p}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
