"""Output normalizer — compact structured JSON from noisy scanner output.

The LLM reads raw text, but the two highest-volume outputs (nmap service
tables, directory brute-force lists) waste context window on boilerplate.
`normalize_output` extracts just the signal as JSON. Pure functions —
no I/O, no state; safe to lift into its own package later unchanged.
"""

from __future__ import annotations

import json
import re

# nmap -sV table lines: "80/tcp open http Apache httpd 2.4.49"
# (also matches "open|filtered" states — still reachable signal)
_NMAP_RE = re.compile(r"^(\d+)/(tcp|udp)\s+open(?:\|filtered)?\s+(\S+)[ \t]*(.*)$", re.MULTILINE)
# gobuster/ffuf result lines: "/admin (Status: 200) [Size: 1234]" or "/admin 200"
_DIR_RE = re.compile(r"^\s*(/[^\s]*)\s+(?:\(Status:\s*(\d+)|(\d+))", re.MULTILINE)
# trailing [Size: N] on dir lines
_SIZE_RE = re.compile(r"\[Size:\s*(\d+)\]")
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+[\w.-]*)")


def parse_nmap(output: str) -> list[dict]:
    """[{port, proto, service, product, version, banner}] per reachable service.

    The full remainder after service is preserved in `banner` — script (NSE)
    output and uncommon banners never get dropped."""
    out = []
    for m in _NMAP_RE.finditer(output or ""):
        port, proto, service, rest = m.groups()
        vm = _VERSION_RE.search(rest)
        out.append(
            {
                "port": int(port),
                "proto": proto,
                "service": service,
                "product": (rest[: vm.start()].strip() if vm else rest.strip())[:60],
                "version": vm.group(1) if vm else "",
                "banner": rest.strip()[:120],
            }
        )
    return out


def parse_dirs(output: str, min_status: int = 200, max_status: int = 399) -> list[dict]:
    """[{path, status, size?}] for paths in the status window. Size kept
    when the tool prints it (gobuster) — useful for spotting real pages."""
    out = []
    for m in _DIR_RE.finditer(output or ""):
        path, s1, s2 = m.groups()
        status = int(s1 or s2 or 0)
        if min_status <= status <= max_status:
            entry = {"path": path, "status": status}
            sm = _SIZE_RE.search(output[m.start() : m.start() + 200])
            if sm:
                entry["size"] = int(sm.group(1))
            out.append(entry)
    return out


def normalize_output(tool_output: str, kind: str = "auto") -> str:
    """Agent-facing: compact JSON from a raw tool dump.

    kind: 'nmap' | 'dirs' | 'auto' (sniffs which parser fits).
    Returns a short JSON string (or a note when nothing matched) — never raises.
    """
    try:
        text = tool_output or ""
        if kind == "auto":
            if _NMAP_RE.search(text):
                kind = "nmap"
            elif _DIR_RE.search(text):
                kind = "dirs"
            else:
                return (
                    "No structure recognized (nmap service table or dir-brute results). "
                    "kind: nmap|dirs forces a parser."
                )
        if kind == "nmap":
            rows = parse_nmap(text)
            return json.dumps(rows) if rows else "[]"
        if kind == "dirs":
            rows = parse_dirs(text)
            return json.dumps(rows) if rows else "[]"
        return f"Unknown kind '{kind}' — use nmap, dirs, or auto."
    except Exception as e:  # normalizing must never break a tool call
        return f"normalize error: {e}"
