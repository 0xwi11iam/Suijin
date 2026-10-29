"""Output normalization — the model reads RESULTS, not noise.

Field-found (2026-10-28 engagement trail, 15/52 iterations truncated):
  - ffuf arg failures dumped their ENTIRE help text into context (4x per
    run) — the sweep "happened" but returned zero usable data
  - fuzzers' progress spam drowned the actual findings
  - structured results (ffuf -of json, JSONL APIs) got byte-capped
    mid-payload instead of EXTRACTED

The normalizer runs on every execute_terminal result:
  1. ffuf usage dumps collapse to a one-line DIAGNOSIS (what broke, which
     flag, what to fix)
  2. ffuf -o json files (when the command wrote them) parse into a
     status/size/URL table — the exact table a human reads
  3. fuzzer/scanner progress lines strip out
  4. giant JSON/JSONL outputs extract to a summary (counts, keys, first/
     last entries) instead of a middle cut
"""

from __future__ import annotations

import contextlib
import json
import os
import re

_FFUF_USAGE_MARKERS = ("Fuzz Faster U Fool", "FUZZING OPTIONS:", "MATCHER OPTIONS:")
_PROGRESS_LINE = re.compile(r"^\s*::.*::\s*$|^\s*\d+/\d+\s*$|^\s*$", re.M)


def _looks_like_ffuf_usage(stdout: str) -> bool:
    return _FFUF_USAGE_MARKERS[0] in stdout and any(m in stdout for m in _FFUF_USAGE_MARKERS[1:])


def _diagnose_ffuf_failure(cmd: str) -> str:
    """One honest line about WHY ffuf printed its help."""
    m = re.search(r"-o\s+(\S+)", cmd)
    if m:
        out_path = m.group(1)
        out_dir = os.path.dirname(os.path.expanduser(out_path)) or "."
        if not os.path.isdir(out_dir):
            return (
                f"[ffuf] ARGUMENT FAILURE — the -o directory does not exist: {out_dir} "
                f"(create it first, or write to $TMPDIR/). ffuf printed its full help text; "
                f"that text is suppressed here."
            )
        if not os.access(out_dir, os.W_OK):
            return (
                f"[ffuf] ARGUMENT FAILURE — the -o directory is not writable: {out_dir} "
                f"(sandbox denies it — use $TMPDIR/ or the engagement home). Full help text suppressed."
            )
    wm = re.search(r"-w\s+(\S+)", cmd)
    if wm and not os.path.exists(os.path.expanduser(wm.group(1))):
        return (
            f"[ffuf] ARGUMENT FAILURE — wordlist not found: {wm.group(1)} "
            f"(use find_wordlist, or wordlist_tool to build one). Full help text suppressed."
        )
    return (
        "[ffuf] ARGUMENT FAILURE — ffuf rejected the arguments and printed its help text "
        "(suppressed). Re-check the flags; most common causes: unwritable -o path, missing "
        "-w wordlist, or a quoted flag value ffuf can't parse."
    )


def _extract_ffuf_json(cmd: str) -> str | None:
    """If the command wrote ffuf JSON output, return the findings table."""
    if "-of" not in cmd and "--output-format" not in cmd:
        return None
    m = re.search(r"(?:-o|--output)\s+(\S+)", cmd)
    if not m:
        return None
    path = os.path.expanduser(m.group(1))
    if not os.path.isfile(path):
        return None
    with contextlib.suppress(Exception):
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = json.loads(fh.read())
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            return "[ffuf] sweep complete — 0 results matched the filters."
        lines = [f"[ffuf] {len(results)} result(s) from {path}:"]
        lines.append(f"{'status':>6}  {'size':>7}  url")
        for r in sorted(results, key=lambda r: (r.get("status", 0), -r.get("length", 0)))[:40]:
            lines.append(f"{r.get('status', '?'):>6}  {r.get('length', '?'):>7}  {r.get('url', '?')}")
        if len(results) > 40:
            lines.append(f"… and {len(results) - 40} more (full data: {path})")
        return "\n".join(lines)
    return None


def _strip_progress(stdout: str) -> str:
    if "::" not in stdout:
        return stdout
    return "\n".join(ln for ln in stdout.splitlines() if not _PROGRESS_LINE.match(ln))


def _extract_structured(stdout: str, limit: int = 2000) -> str | None:
    """Giant JSON/JSONL becomes a summary, never a middle cut."""
    if len(stdout) < limit or stdout.lstrip()[:1] not in "{[":
        return None
    candidates: list[dict] = []
    with contextlib.suppress(Exception):
        parsed = json.loads(stdout)
        if isinstance(parsed, dict):
            for key in ("results", "items", "data", "endpoints", "paths", "vulnerabilities"):
                if isinstance(parsed.get(key), list):
                    candidates = parsed[key]
                    meta = {k: v for k, v in parsed.items() if k != key and isinstance(v, (str, int, float, bool))}
                    break
        elif isinstance(parsed, list):
            candidates = [c for c in parsed if isinstance(c, dict)]
            meta = {"entries": len(parsed)}
    if not candidates:
        # JSONL fallback
        lines = [ln for ln in stdout.splitlines() if ln.lstrip()[:1] in "{["]
        if len(lines) >= 10:
            with contextlib.suppress(Exception):
                candidates = [json.loads(ln) for ln in lines if isinstance(json.loads(ln), dict)]
                meta = {"jsonl_lines": len(lines)}
    if not candidates:
        return None
    keys = sorted({k for c in candidates[:50] for k in c if isinstance(c.get(k), (str, int, float, bool))})
    out = [f"[structured] {len(candidates)} entries — keys: {', '.join(keys[:8])} — meta: {json.dumps(meta)[:200]}"]
    out.append("first:")
    out.append(json.dumps(candidates[0], indent=1)[:400])
    out.append("last:")
    out.append(json.dumps(candidates[-1], indent=1)[:400])
    out.append(f"(full data preserved in the audit trail — {len(stdout)} chars)")
    return "\n".join(out)


def normalize_output(cmd: str, out: str) -> str:
    """Apply all normalizations to a formatted terminal result."""
    if not out:
        return out
    # only touch STDOUT sections for the heavy transforms
    m = re.search(r"(\[STDOUT\]\n)(.*?)(\n\[STDERR\]|\Z)", out, re.S)
    if not m:
        return out
    stdout = m.group(2)

    if "ffuf" in cmd:
        if _looks_like_ffuf_usage(stdout):
            stdout = _diagnose_ffuf_failure(cmd)
        else:
            table = _extract_ffuf_json(cmd)
            stdout = table or _strip_progress(stdout)

    extracted = _extract_structured(stdout)
    if extracted:
        stdout = extracted

    return out[: m.start(2)] + stdout + out[m.end(2) :]


def heal_command(cmd: str, home: str) -> tuple[str, list[str]]:
    """Pre-execution path healing. Returns (healed_cmd, notes_for_model)."""
    notes: list[str] = []
    healed = cmd

    # 1. the RETIRED v7.2 workspace path — 54 uses in one trail, every one
    #    bounced off the sandbox jail. Rewrite to the engagement home.
    retired = re.compile(r"(?:~|/Users/[^\s/'\"]+|/home/[^\s/'\"]+)?/?\.?suijin/workspace(?P<rest>/\S*)")
    if retired.search(healed):
        healed = retired.sub(lambda m: home + (m.group("rest") or ""), healed)
        notes.append(
            "[sandbox] NOTE: you used the RETIRED ~/.suijin/workspace path — rewritten to your "
            "engagement home. Canonical scratch dirs: $TMPDIR (exists now), $SUIJIN_HOME (your home)."
        )

    # 2. redirects quoted INSIDE a path — the model's shell bug
    #    (cd '/path/engages 2>/dev/null' — the redirect became part of
    #    the quoted string and the cd failed on a nonexistent dir)
    for q in ("'", '"'):
        for m in re.finditer(
            re.escape(q)
            + r"([^"
            + re.escape(q)
            + r"]*?)(2>/dev/null|\bdone\b[;&|])[^"
            + re.escape(q)
            + r"]*"
            + re.escape(q),
            healed,
        ):
            frag = m.group(0)
            if "2>/dev/null" in m.group(2):
                notes.append(
                    f"[shell] NOTE: a redirect (2>/dev/null) is INSIDE the quoted path {frag[:60]}… — "
                    f"move it OUTSIDE the quotes; as written the redirect is part of the filename."
                )
    return healed, notes
