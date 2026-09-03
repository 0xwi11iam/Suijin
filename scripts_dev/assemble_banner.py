"""Assemble the dragon banner: parse the ansifier-style source into a
run-length grid JSON consumed by banner.py.

Source format (/tmp/dragon_src.html): the pasted HTML where every span is
copied VERBATIM, plus 'Rn' tokens meaning n default cells (bg #000000,
fg #800000, space) — pure-black runs need no transcription.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SPAN_RE = re.compile(
    r'<span style="background-color:(#[0-9a-f]{6});color: ?(#[0-9a-f]{6});">([^<]*)</span>'
)
DEFAULT = {"ch": " ", "bg": "#000000", "fg": "#800000"}


BANNER_WIDTH = 75


def parse_source(src: str) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for line in src.splitlines():
        line = line.strip()
        if not line or line.startswith("<html") or line.startswith("</span></code>") or "<style>" in line:
            continue
        if line.startswith("<code>") or line.startswith("</code></html>"):
            continue
        row: list[dict] = []
        # tokenize: Rn markers and spans
        pos = 0
        token_re = re.compile(r"R(\d+)|R\*|(<span )")
        for m in token_re.finditer(line):
            if m.group(1):
                row.extend([dict(DEFAULT)] * int(m.group(1)))
            elif m.group(0) == "R*":
                row.extend([dict(DEFAULT)] * (75 - len(row)))
            else:
                sm = SPAN_RE.match(line, m.start())
                if not sm:
                    print(f"UNPARSED SPAN at row {len(rows)}: {line[m.start():m.start()+80]!r}", file=sys.stderr)
                    sys.exit(1)
                bg, fg, ch = sm.group(1), sm.group(2), sm.group(3)
                row.append({"ch": ch, "bg": bg, "fg": fg})
                pos = sm.end()
        if row:
            rows.append(row)
    return rows


def compress(rows: list[list[dict]]) -> dict:
    width = max(len(r) for r in rows)
    # pad all rows to width with the default cell
    for r in rows:
        while len(r) < width:
            r.append(dict(DEFAULT))
    out_rows = []
    for r in rows:
        runs = []
        i = 0
        while i < len(r):
            c = r[i]
            j = i
            while j < len(r) and r[j] == c:
                j += 1
            run = [j - i, c["ch"], c["bg"], c["fg"]]
            runs.append(run)
            i = j
        out_rows.append(runs)
    return {"width": width, "height": len(rows), "default": DEFAULT, "rows": out_rows}


if __name__ == "__main__":
    src = Path("/tmp/dragon_src.html").read_text()
    rows = parse_source(src)
    bad = [(i + 1, len(r)) for i, r in enumerate(rows) if len(r) != 75]
    if bad:
        print(f"WIDTH VIOLATIONS (row, width): {bad}", file=sys.stderr)
        sys.exit(1)
    if not (20 <= len(rows) <= 40):
        print(f"SUSPECT ROW COUNT: {len(rows)}", file=sys.stderr)
    widths = {len(r) for r in rows}
    grid = compress(rows)
    out = Path("suijin/assets/dragon_banner.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(grid))
    # glyph-only preview for eyeballing
    preview = []
    for r in rows:
        preview.append("".join(c["ch"] for c in r).replace(" ", "·"))
    Path("/tmp/dragon_preview.txt").write_text("\n".join(preview))
    print(f"rows={len(rows)} widths={sorted(widths)} grid={grid['width']}x{grid['height']} bytes={out.stat().st_size}")
