"""Assemble the new bg-only banner from segment transcription.

Segment format (one row per line, whitespace-separated):
  `27z 66 246 241 9z 109 248 146`  —  27 black cells, then colored bgs,
  `9z` = 9 black cells. Trailing black cells are implicit (padded).
Row width must be exactly 100 cells (validated).

Output: frame-stripped (black bg-0 cells → plain spaces), cropped to the
art bounding box, same-bg runs merged, ESC[48;5;Nm per run, [0m per row.
"""

from __future__ import annotations

import re
import sys

WIDTH = 100


def parse(path: str) -> list[list[int]]:
    rows = []
    for i, line in enumerate(open(path), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cells: list[int] = []
        if "[48;5;" in line:
            # verbatim token format: [48;5;Nm + one char (space); [0m ends
            for m in re.finditer(r"\[48;5;(\d+)m(.)", line):
                cells.append(int(m.group(1)))
        else:
            for tok in line.split():
                m = re.fullmatch(r"(\d+)z", tok)
                if m:
                    cells.extend([0] * int(m.group(1)))
                else:
                    cells.append(int(tok))
        if len(cells) > WIDTH:
            print(f"row {i}: {len(cells)} cells > {WIDTH} — count error", file=sys.stderr)
            sys.exit(1)
        cells.extend([0] * (WIDTH - len(cells)))
        rows.append(cells)
    return rows


def build(rows: list[list[int]]) -> str:
    # crop to bounding box of colored cells
    ys = [y for y, r in enumerate(rows) if any(c != 0 for c in r)]
    if not ys:
        print("no art", file=sys.stderr)
        sys.exit(1)
    xs = [x for x in range(WIDTH) if any(r[x] != 0 for r in rows)]
    x0, x1 = min(xs), max(xs)
    out_lines = []
    for y in range(min(ys), max(ys) + 1):
        parts: list[str] = []
        spaces = 0
        for x in range(x0, x1 + 1):
            c = rows[y][x]
            if c == 0:
                spaces += 1
                continue
            if spaces:
                parts.append(" " * spaces)
                spaces = 0
            parts.append(f"\x1b[48;5;{c}m ")
        line = "".join(parts)
        out_lines.append((line + "\x1b[0m").rstrip() if parts else "")
    while out_lines and not out_lines[0]:
        out_lines.pop(0)
    while out_lines and not out_lines[-1]:
        out_lines.pop()
    return "\n".join(out_lines) + "\n"


if __name__ == "__main__":
    rows = parse("/tmp/nd_segments.txt")
    print(f"rows: {len(rows)}", file=sys.stderr)
    art = build(rows)
    open("suijin/assets/banner.ans", "w").write(art)
    n_rows = len(art.rstrip("\n").split("\n"))
    xs_len = None
    # recompute cropped width for the gate
    ys = [y for y, r in enumerate(rows) if any(c != 0 for c in r)]
    xs = [x for x in range(WIDTH) if any(r[x] != 0 for r in rows)]
    print(f"banner.ans: {len(art)} bytes, {n_rows} rows, art width {max(xs)-min(xs)+1}", file=sys.stderr)
