import hashlib
import re
import zipfile
from pathlib import Path


def _ws():
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR


def file_tree(subdir: str = "") -> str:
    base = _ws() / (subdir.strip("/") if subdir else "")
    if not base.is_dir():
        return f"Error: {base} not found"
    rows = []
    total = 0
    count = 0
    for p in sorted(base.rglob("*")):
        if count >= 400:
            rows.append("... (capped at 400 entries)")
            break
        if any(part in (".venv", "__pycache__", "node_modules") for part in p.parts):
            continue
        rel = p.relative_to(_ws())
        if p.is_file():
            size = p.stat().st_size
            total += size
            rows.append(f"{size:>10,}B  {rel}")
            count += 1
        elif p.is_dir():
            rows.append(f"{'<dir>':>10}  {rel}/")
            count += 1
    return f"{base}\n" + "\n".join(rows) + f"\n(total files {total:,} bytes)"


def file_grep(pattern: str = "", subdir: str = "") -> str:
    if not pattern:
        return "Error: pattern required"
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"Error: bad regex: {e}"
    base = _ws() / (subdir.strip("/") if subdir else "")
    if not base.is_dir():
        return f"Error: {base} not found"
    hits = []
    for p in sorted(base.rglob("*")):
        if len(hits) >= 50:
            hits.append("... (capped at 50)")
            break
        if (
            not p.is_file()
            or p.stat().st_size > 2_000_000
            or any(x in p.suffix for x in (".zip", ".gz", ".sqlite3", ".db"))
        ):
            continue
        try:
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{p.relative_to(_ws())}:{i}: {line.strip()[:100]}")
                    if len(hits) >= 50:
                        break
        except OSError:
            continue
    return "\n".join(hits) or f"No matches for {pattern!r}"


def file_stat(path: str = "") -> str:
    if not path:
        return "Error: path required"
    p = Path(path)
    f = p if p.is_absolute() else _ws() / path.strip("/")
    if not f.exists():
        return f"Error: {f} not found"
    st = f.stat()
    import time as _t

    out = [f"{f}", f"  size={st.st_size:,}B  modified={_t.strftime('%Y-%m-%d %H:%M:%S', _t.localtime(st.st_mtime))}"]
    if f.is_file():
        h = hashlib.sha256()
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        out.append(f"  sha256={h.hexdigest()}")
    return "\n".join(out)


def archive_extract(path: str = "", list_only: str = "", dest: str = "") -> str:
    if not path:
        return "Error: path required"
    p = Path(path)
    f = p if p.is_absolute() else _ws() / path.strip("/")
    if not f.is_file():
        return f"Error: {f} not found"
    try:
        z = zipfile.ZipFile(f)
    except zipfile.BadZipFile:
        return "Error: not a valid zip"
    names = z.namelist()
    if (list_only or "").lower().startswith("t"):
        return f"{len(names)} entries:\n  " + "\n  ".join(names[:100])
    target_dir = _ws() / (dest.strip("/") if dest else "archives") / f.stem
    target_dir.mkdir(parents=True, exist_ok=True)
    root = target_dir.resolve()
    extracted = 0
    for info in z.infolist():
        if info.is_dir():
            continue
        out_path = (target_dir / info.filename).resolve()
        if not str(out_path).startswith(str(root)):  # zip-slip guard
            return f"Error: blocked path traversal entry {info.filename!r}"
        if extracted >= 2000:
            break
        z.extract(info, target_dir)
        extracted += 1
    return f"extracted {extracted} files -> {target_dir}"
