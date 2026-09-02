"""web_session — the cross-credential session model (Wave 3).

Deterministic extraction over captured traffic (no LLM analyzer — the
study's verdict: code beats the model for this). Every governed send
(http_replay / inject_probe) records WHAT was sent under WHICH
credential; the summary builds the cross-credential shortlist (endpoints
reached by 2+ credentials — the IDOR candidate list) and the
hidden-params correlation (request fields the UI never exposed — the
mass-assignment targeting list).

Guided role cycling: the operator/agent registers credentials
(register_credential), browses as each — every send is attributed
automatically. The shortlist IS the access-control worklist.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

_LOCK = threading.Lock()
_UI_FIELDS: dict[str, list[dict]] = {}  # url-path -> [{name, hidden, readonly, disabled, type}]
_LAST = {"observations": []}  # ring of the most recent observations (context block source)

_SENSITIVE_FIELDS = ("password", "passwd", "token", "secret", "api_key", "apikey", "ssn", "credit_card", "private_key")
_ID_HINT = re.compile(r"(^|_)(id|uuid|guid|uid|ref|num|number|no)$", re.I)


def _cred_of(req: dict) -> str:
    """Which credential sent this? Match registered credential headers,
    else a hash of the auth header value, else 'anon'."""
    headers = {k.lower(): v for k, v in dict(req.get("headers") or {}).items()}
    for key in ("x-session", "authorization", "cookie", "x-auth-token", "x-api-key"):
        v = headers.get(key)
        if v:
            return f"{key}:{str(v)[:24]}"  # stable short label
    return "anon"


def _params_of(req: dict) -> dict:
    out: dict = {}
    parts = urlsplit(req.get("url") or "")
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        out[k] = v
    body = str(req.get("body") or "")
    if body.strip().startswith("{"):
        try:
            d = json.loads(body)
            if isinstance(d, dict):
                for k, v in d.items():
                    out[k] = v if isinstance(v, (str, int, float)) else json.dumps(v)[:60]
        except Exception:  # noqa: BLE001
            pass
    else:
        for k, v in parse_qsl(body, keep_blank_values=True):
            out[k] = v
    return out


def record_send(req: dict) -> None:
    """Called by the governed engine after every send — the automatic
    observation layer. Never raises."""
    try:
        parts = urlsplit(req.get("url") or "")
        endpoint = f"{req.get('method', 'GET')} {parts.path}"
        obs = {
            "credential": _cred_of(req),
            "endpoint": endpoint,
            "params": _params_of(req),
        }
        with _LOCK:
            _LAST["observations"].append(obs)
            _LAST["observations"] = _LAST["observations"][-500:]
            p = _store_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a") as f:
                f.write(json.dumps(obs, default=str) + "\n")
    except Exception:  # noqa: BLE001 — observation must never break a send
        pass


def _store_path() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR, engagement_dir

    base = Path(engagement_dir() or (WORKSPACE_DIR / "outputs" / "engagements" / "_default"))
    return base / "web_session.jsonl"


def record_ui_fields(url: str, fields: list[dict]) -> None:
    """mcp_playwright snapshot calls this: the UI's form-field truth
    (name + hidden/readonly/disabled) per page."""
    try:
        parts = urlsplit(url or "")
        with _LOCK:
            _UI_FIELDS[parts.path] = list(fields or [])[:40]
    except Exception:  # noqa: BLE001
        pass


def _observations() -> list[dict]:
    try:
        with open(_store_path()) as fh:
            rows = []
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
            return rows[-500:]
    except Exception:  # noqa: BLE001
        return []


def _endpoint_key(endpoint: str) -> str:
    """Shape key: id-like path segments collapsed — /api/docs/d-8b2e40d1 and
    /api/docs/d-9c5f12ab are the same endpoint shape. An id-like segment
    is ≥4 chars containing at least one digit (catches numeric ids, uuids,
    hex slugs; keeps /v2, /api)."""
    s = re.sub(r"/(?=[A-Za-z0-9_-]{4,}(/|$))(?=[^/]*\d)[A-Za-z0-9_-]+(?=/|$)", "/:id", str(endpoint))
    return re.sub(r"/\d+(?=/|$)", "/:id", s)  # short numeric ids too


def cross_credential_shortlist() -> list[dict]:
    """Endpoints whose SHAPE was reached by 2+ distinct credentials —
    THE access-control worklist. Each entry carries observed param values
    per credential (the IDOR substrate)."""
    shapes: dict[str, dict] = {}
    for o in _observations():
        key = _endpoint_key(o.get("endpoint") or "")
        slot = shapes.setdefault(key, {"endpoint_shape": key, "credentials": {}, "examples": []})
        cred = o.get("credential") or "anon"
        cslot = slot["credentials"].setdefault(cred, {})
        for k, v in dict(o.get("params") or {}).items():
            vals = cslot.setdefault(k, [])
            sv = str(v)[:60]
            if sv not in vals and len(vals) < 6:
                vals.append(sv)
        if len(slot["examples"]) < 4 and o.get("endpoint"):
            slot["examples"].append(o["endpoint"])
    out = []
    for slot in shapes.values():
        creds = slot["credentials"]
        if len(creds) >= 2:
            differing_ids = []
            for field, per_cred in _field_matrix(creds).items():
                if _ID_HINT.search(field) or field.lower().endswith(("id", "ref")):
                    distinct = {v for vs in per_cred.values() for v in vs}
                    if len(distinct) >= 2:
                        differing_ids.append({"field": field, "values_by_credential": per_cred})
            out.append({
                "endpoint_shape": slot["endpoint_shape"],
                "credentials": sorted(creds),
                "observed_examples": slot["examples"],
                "id_fields_differing_by_credential": differing_ids[:4],
            })
    return sorted(out, key=lambda s: -len(s["id_fields_differing_by_credential"]))[:12]


def _field_matrix(creds: dict) -> dict:
    m: dict[str, dict] = {}
    for cred, params in creds.items():
        for k, vals in params.items():
            m.setdefault(k, {})[cred] = vals
    return m


def hidden_params() -> list[dict]:
    """Request params the UI never exposed (mass-assignment targets) +
    UI fields flagged hidden that DO reach requests (leaks)."""
    out = []
    obs = _observations()
    ui_paths = dict(_UI_FIELDS)
    if not ui_paths:
        return []
    all_ui_names = {f.get("name", "").lower() for fields in ui_paths.values() for f in fields}
    for o in obs[-120:]:
        sent = {k.lower() for k in (o.get("params") or {})}
        if not sent:
            continue
        parts = re.sub(r"^(\w+) ", "", o.get("endpoint") or "")
        ui_names = set()
        for path, fields in ui_paths.items():
            if path.rstrip("/").endswith(parts.rstrip("/").split("/")[-1]) or parts.rstrip("/").endswith(path.rstrip("/")):
                ui_names |= {f.get("name", "").lower() for f in fields}
        if not ui_names:
            ui_names = all_ui_names
        hidden = sorted(sent - ui_names - {"csrf_token", "authenticity_token", "_token"})
        if hidden:
            out.append({"endpoint": o.get("endpoint"), "params_not_in_ui": hidden[:8],
                        "credential": o.get("credential")})
    # dedupe by endpoint+params
    seen = set()
    deduped = []
    for e in out:
        k = (e["endpoint"], tuple(e["params_not_in_ui"]))
        if k not in seen:
            seen.add(k)
            deduped.append(e)
    return deduped[:10]


def web_session(action: str = "summary") -> str:
    """The access-control worklist. summary = the cross-credential
    shortlist + hidden params; observations = the raw recent capture."""
    try:
        act = str(action or "summary").lower()
        if act == "observations":
            obs = _observations()[-30:]
            return json.dumps(obs, indent=2, default=str)[:5000]
        shortlist = cross_credential_shortlist()
        hp = hidden_params()
        lines = ["WEB SESSION MODEL — access-control worklist"]
        if not shortlist:
            lines.append("(no cross-credential endpoints yet — register credentials and replay "
                         "the same surfaces as each; every governed send is captured automatically)")
        for s in shortlist:
            lines.append(
                f"  ▸ {s['endpoint_shape']}  reached by {len(s['credentials'])} credentials "
                f"({', '.join(s['credentials'][:4])})"
            )
            for f in s["id_fields_differing_by_credential"]:
                per = "; ".join(f"{c.split(':')[0]}={'|'.join(v[:3])}" for c, v in f["values_by_credential"].items())
                lines.append(f"      ID field {f['field']}: {per}")
            lines.append("      → IDOR test: replay with the OTHER credential's id value (http_replay "
                         "compare:{mutations:[set-query/body-set-field], credential:...})")
        if hp:
            lines.append("HIDDEN PARAMS (in requests, never in the UI — mass-assignment targets):")
            for h in hp[:6]:
                lines.append(f"  ▸ {h['endpoint']}: {', '.join(h['params_not_in_ui'])}")
        return "\n".join(lines)[:5000]
    except Exception as e:  # noqa: BLE001
        return f"Error: web_session failed: {e}"
