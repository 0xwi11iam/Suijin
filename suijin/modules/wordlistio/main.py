import random
import re
from pathlib import Path


def merge_wordlists(files: str = "", pattern: str = "", out: str = "wordlists/merged.txt") -> str:
    if not files:
        return "Error: files required (comma-separated paths)"
    keep = re.compile(pattern) if pattern else None
    seen = set()
    total = 0
    for f in files.split(","):
        p = Path(f.strip()).expanduser()
        if not p.is_file():
            return f"Error: {p} not found"
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    w = line.rstrip("\r\n")
                    total += 1
                    if w and w not in seen and (keep is None or keep.search(w)):
                        seen.add(w)
        except OSError as e:
            return f"Error reading {p}: {e}"
    from suijin.modules.platform.lib.workspace import resolve_workspace_path

    target = Path(out) if Path(out).is_absolute() else resolve_workspace_path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")
    return f"merged {total:,} lines from {len(files.split(','))} files -> {len(seen):,} unique at {target}"


def sample_wordlist(file: str = "", n: int = 10) -> str:
    if not file:
        return "Error: file required"
    p = Path(file.strip()).expanduser()
    if not p.is_file():
        return f"Error: {p} not found"
    try:
        lines = [x for x in p.read_text(encoding="utf-8", errors="ignore").splitlines() if x]
    except OSError as e:
        return f"Error: {e}"
    if not lines:
        return "Empty wordlist"
    n = max(1, min(int(n or 10), 100))
    sample = random.sample(lines, min(n, len(lines)))
    avg = sum(len(x) for x in lines) // len(lines)
    return f"{len(lines):,} lines, avg len {avg}\nsample:\n  " + "\n  ".join(sample)
