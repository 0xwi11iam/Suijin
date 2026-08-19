import json
import shlex
from pathlib import Path

import requests


def raw_request_parse(raw: str = "") -> str:
    if not raw.strip():
        return "Error: raw request text required"
    try:
        head, _, body = raw.partition("\n\n")
        if not body and "\r\n\r\n" in raw:
            head, _, body = raw.partition("\r\n\r\n")
        lines = head.splitlines()
        method, target, _proto = lines[0].split(" ", 2)
        headers = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip()] = v.strip()
        return json.dumps({"method": method, "target": target, "headers": headers, "body": body[:2000]}, indent=2)
    except (ValueError, IndexError):
        return "Error: not a parseable HTTP request (need 'METHOD target HTTP/x.y' first line)"


def curl_build(method: str = "GET", url: str = "", headers: str = "", body: str = "") -> str:
    if not url:
        return "Error: url required"
    parts = ["curl", "-sk", "-X", (method or "GET").upper(), shlex.quote(url.strip())]
    try:
        hdr = json.loads(headers) if headers else {}
    except ValueError:
        return "Error: headers must be a JSON object"
    for k, v in hdr.items():
        parts += ["-H", shlex.quote(f"{k}: {v}")]
    if body:
        parts += ["--data-raw", shlex.quote(body)]
    return " ".join(parts)


def http_download(url: str = "", out: str = "") -> str:
    if not url:
        return "Error: url required"
    from suijin.modules.platform.lib.workspace import resolve_workspace_path

    target = resolve_workspace_path(out or "downloads/" + url.rstrip("/").split("/")[-1].split("?")[0])
    try:
        r = requests.get(
            url.strip(), timeout=(5, 60), stream=True, headers={"User-Agent": "Mozilla/5.0 (suijin download)"}
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return f"Error: {e}"
    cap = 50 * 1024 * 1024
    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as fh:
        for chunk in r.iter_content(65536):
            size += len(chunk)
            if size > cap:
                return f"Error: exceeded {cap // (1024 * 1024)}MB cap"
            fh.write(chunk)
    return f"saved {size:,} bytes -> {target}"
