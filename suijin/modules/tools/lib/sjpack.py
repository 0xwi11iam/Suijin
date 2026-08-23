"""sj? packages — the person-to-person distribution format.

Three extensions, one sealed-zip container spec:

    .sjm  module pack   (manifest.json + main.py + skill.md)  -> ~/.suijin/modules/<id>/
    .sja  addon         (bare main.py, zero boilerplate)      -> suijin/addons/<id>/
    .sjp  kernel plugin (plugin.json + lib/)                  -> ~/.suijin/modules/<id>/ (tier-gated)

Container layout:
    sjpkg.json    — metadata: kind/id/version/author/built_at/dev_note/description,
                    tools[], external_binaries[], tier, advisory scan report
    SHA256SUMS    — "<sha256>  <relpath>" per payload file (tamper seal)
    <payload...>  — the pack source itself

Guards (every one tested):
    - SHA256SUMS verification — any mismatch refuses and names the file
    - path traversal — absolute paths, '..' segments, symlink entries refused
    - zip bombs — uncompressed caps (total bytes, file count)
    - tool shadowing — public tools colliding with built-ins refuse (the
      loader registers into a flat namespace; a shadowed http_request is
      a supply-chain takeover)
    - folder shadowing — ids matching existing module dirs refuse
    - tier gate — .sjp with tier=core refuses (community is recommended max)
    - safety scan — ALWAYS re-scanned from source at install (the embedded
      report is advisory for browsing, never trusted); critical => refuse
      unless allow_unsafe
Zero new dependencies: stdlib zipfile/hashlib/ast only.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

FORMAT_VERSION = 1
KIND_BY_EXT = {".sjm": "module", ".sja": "addon", ".sjp": "plugin"}
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50MB uncompressed — beyond this is a bomb or a mistake
MAX_FILE_COUNT = 500

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _workspace_out(name: str) -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    d = WORKSPACE_DIR / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _stats() -> dict:
    p = _workspace_out("pack_stats.json")
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {"built": {}, "installed": {}}


def _bump(counter: str, pkg_id: str) -> None:
    with contextlib.suppress(Exception):  # telemetry must never break an install
        s = _stats()
        s.setdefault(counter, {})
        s[counter][pkg_id] = int(s[counter].get(pkg_id, 0)) + 1
        _workspace_out("pack_stats.json").write_text(json.dumps(s, indent=2))


# ── tool-table extraction (build side) ──────────────────────────────────


def extract_tool_table(main_py_source: str) -> list[dict]:
    """Public functions + docstring + args — the honest tools table."""
    out = []
    try:
        tree = ast.parse(main_py_source)
    except SyntaxError:
        return out
    for stmt in tree.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) or stmt.name.startswith("_"):
            continue
        doc = (ast.get_docstring(stmt) or "").strip().split("\n")[0][:100]
        args = [a.arg for a in stmt.args.args if a.arg != "self"]
        out.append({"name": stmt.name, "description": doc, "args": args})
    return out


def _external_binaries(source: str) -> list[str]:
    """Binaries the pack shells out to — extracted for honest metadata."""
    bins = set()
    for m in re.finditer(r"(?:subprocess|os)\.\w+\(\s*\[?\s*[\"']([a-z0-9_.-]+)[\"']", source):
        name = m.group(1)
        if name not in ("python", "python3", "sh", "bash"):
            bins.add(name)
    for m in re.finditer(r"(?:cmd|command)\s*=\s*[\"']([a-z0-9_.-]{2,20})\s", source):
        bins.add(m.group(1))
    return sorted(bins)[:20]


# ── build ───────────────────────────────────────────────────────────────


def infer_kind(dir_path: Path) -> tuple[str, str]:
    """(kind, id) from the payload shape."""
    d = Path(dir_path)
    if (d / "plugin.json").exists() and not (d / "manifest.json").exists():
        meta = json.loads((d / "plugin.json").read_text())
        return "plugin", str(meta.get("id") or d.name)
    if (d / "manifest.json").exists():
        meta = json.loads((d / "manifest.json").read_text())
        return "module", str(meta.get("name") or d.name)
    if (d / "main.py").exists():
        return "addon", d.name
    raise ValueError(f"not a package source dir (need manifest.json | plugin.json | main.py): {d}")


def _payload_files(d: Path) -> list[Path]:
    skip = {"__pycache__", ".git", ".pytest_cache"}
    return sorted(p for p in d.rglob("*") if p.is_file() and not (skip & set(p.parts)))


def build(dir_path, note: str = "", out: str = "", author: str = "") -> dict:
    """Build a sealed .sj? archive. Returns {path, sha256, kind, id}."""
    d = Path(dir_path).expanduser().resolve()
    if not d.is_dir():
        return {"error": f"source dir not found: {d}"}
    kind, pkg_id = infer_kind(d)
    if not _SAFE_NAME_RE.match(pkg_id):
        return {"error": f"unsafe package id {pkg_id!r}"}

    manifest = {}
    if (d / "manifest.json").exists():
        manifest = json.loads((d / "manifest.json").read_text())
    elif (d / "plugin.json").exists():
        manifest = json.loads((d / "plugin.json").read_text())
    # implementation file: root main.py (module/addon) or lib/main.py (plugin)
    impl = next((p for p in (d / "main.py", d / "lib" / "main.py") if p.exists()), None)
    main_src = impl.read_text() if impl else ""
    if not main_src:
        return {"error": "no main.py — nothing to install"}

    from suijin.modules.platform.lib.safety.scan import scan_sources

    sources = {str(p.relative_to(d)): p.read_text(errors="ignore") for p in _payload_files(d) if p.suffix == ".py"}
    scan = scan_sources(sources)
    tools = extract_tool_table(main_src)
    if not tools and kind != "plugin":
        # plugins expose the register/start/stop lifecycle, not tools
        return {"error": "main.py exposes no public functions — nothing for the agent to call"}

    meta = {
        "format": FORMAT_VERSION,
        "kind": kind,
        "id": pkg_id,
        "name": str(manifest.get("title") or manifest.get("name") or pkg_id),
        "version": str(manifest.get("version") or "1.0"),
        "author": author or str(manifest.get("author") or "unknown"),
        "built_at": _now(),
        "dev_note": note.strip(),
        "description": str(manifest.get("description") or ""),
        "tools": tools,
        "external_binaries": _external_binaries("".join(sources.values())),
        "tier": str(manifest.get("tier") or "recommended"),
        "source_url": str(manifest.get("source_url") or ""),
        "advisory_scan": scan,
    }

    ext = {v: k for k, v in KIND_BY_EXT.items()}[kind]
    out_path = Path(out).expanduser() if out else d.parent / "built" / f"{pkg_id}-{meta['version']}{ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    files = _payload_files(d)
    # manifest hygiene: the loader contract requires a non-empty `tools`
    # map — auto-fill from the extracted table so authors can't ship an
    # invalid manifest (build-time generation from actual code)
    manifest_path = d / "manifest.json"
    if kind == "module" and manifest_path.exists():
        try:
            on_disk = json.loads(manifest_path.read_text())
        except ValueError:
            on_disk = {}
        if not isinstance(on_disk.get("tools"), dict) or not on_disk.get("tools"):
            on_disk["tools"] = {
                t["name"]: {"description": t["description"], "parameters": {a: "value" for a in t.get("args", [])}}
                for t in tools
            }
            patched = json.dumps(on_disk, indent=2)
            zf_manifest = patched  # written instead of the raw file below
        else:
            zf_manifest = None
    else:
        zf_manifest = None

    sums = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            rel = str(p.relative_to(d))
            data = zf_manifest.encode() if (zf_manifest is not None and rel == "manifest.json") else p.read_bytes()
            zf.writestr(rel, data)
            sums.append(f"{hashlib.sha256(data).hexdigest()}  {rel}")
        zf.writestr("SHA256SUMS", "\n".join(sums) + "\n")
        zf.writestr("sjpkg.json", json.dumps(meta, indent=2))

    _bump("built", pkg_id)
    return {
        "path": str(out_path),
        "sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
        "kind": kind,
        "id": pkg_id,
        "tools": len(tools),
    }


# ── inspect / install ───────────────────────────────────────────────────


def inspect(path) -> dict:
    """Open a container, verify the seal, return metadata + verdicts."""
    p = Path(path).expanduser()
    if not p.exists():
        return {"error": f"file not found: {p}"}
    if p.suffix not in KIND_BY_EXT:
        return {"error": f"unknown extension {p.suffix!r} (one of {sorted(KIND_BY_EXT)})"}
    try:
        with zipfile.ZipFile(p) as zf:
            meta = json.loads(zf.read("sjpkg.json"))
            sums = zf.read("SHA256SUMS").decode()
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        return {"error": f"not a valid sj? container: {e}"}
    verdict, problem = _verify(zf_meta=meta, sums=sums, zf=zipfile.ZipFile(p))
    return {"meta": meta, "seal": verdict, "seal_problem": problem, "path": str(p)}


def _verify(zf_meta, sums, zf) -> tuple[str, str]:
    """Seal check: path safety + caps + symlinks + hashes. ('ok','') or ('bad', why)."""
    names = zf.namelist()
    if len(names) > MAX_FILE_COUNT:
        return "bad", f"too many entries ({len(names)} > {MAX_FILE_COUNT})"
    total = sum(i.file_size for i in zf.infolist())
    if total > MAX_TOTAL_BYTES:
        return "bad", f"uncompressed size {total} exceeds {MAX_TOTAL_BYTES} cap"
    # path safety: absolute paths and '..' segments are traversal attempts
    for name in names:
        parts = name.split("/")
        if name.startswith("/") or ".." in parts or (parts and parts[0] == ""):
            return "bad", f"unsafe path in archive: {name!r}"
    for info in zf.infolist():
        # symlink entries: high bits of external_attr hold unix mode; S_IFLNK=0xA000
        if (info.external_attr >> 16) & 0xF000 == 0xA000:
            return "bad", f"symlink entry refused: {info.filename!r}"
    # hash verification
    expected = {}
    for line in sums.splitlines():
        line = line.strip()
        if not line:
            continue
        h, _, rel = line.partition("  ")
        expected[rel] = h
    payload = [n for n in names if n not in ("sjpkg.json", "SHA256SUMS")]
    for rel in payload:
        if rel not in expected:
            return "bad", f"unsealed file (not in SHA256SUMS): {rel}"
        actual = hashlib.sha256(zf.read(rel)).hexdigest()
        if actual != expected[rel]:
            return "bad", f"tampered file: {rel}"
    return "ok", ""


def _reserved_names() -> set:
    """Built-in tool ids + existing module folder ids — shadow guards."""
    reserved = set()
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.dispatch import _build_routes

        reserved |= set(_build_routes({}).keys())
    with contextlib.suppress(Exception):
        from suijin.modules.loader import PACK_ROOTS

        for root in PACK_ROOTS:
            if root.is_dir():
                reserved |= {f.name for f in root.iterdir() if f.is_dir()}
    return reserved


def install(path, yes: bool = False, allow_unsafe: bool = False, console=None) -> dict:
    """Verify → re-scan → wizard → extract → validate. Returns result dict
    ({"error": ...} on refusal — never raises)."""
    from rich.console import Console

    con = console or Console()
    p = Path(path).expanduser()
    info = inspect(p)
    if "error" in info:
        return info
    if info["seal"] != "ok":
        return {"error": f"integrity check failed: {info['seal_problem']}"}
    meta = info["meta"]
    kind = meta.get("kind")
    ext_kind = KIND_BY_EXT.get(p.suffix)
    if ext_kind != kind:
        return {"error": f"extension .{p.suffix} implies {ext_kind} but metadata says {kind}"}
    if int(meta.get("format", 0)) != FORMAT_VERSION:
        return {"error": f"unsupported format version {meta.get('format')} (want {FORMAT_VERSION})"}
    if kind == "plugin" and str(meta.get("tier", "")).lower() == "core":
        return {"error": "tier=core plugins cannot be user-installed (community max is 'recommended')"}
    pkg_id = str(meta.get("id", ""))
    if not _SAFE_NAME_RE.match(pkg_id):
        return {"error": f"unsafe package id {pkg_id!r}"}

    # ALWAYS re-scan source — the embedded advisory report is never trusted
    from suijin.modules.platform.lib.safety.scan import scan_sources

    with zipfile.ZipFile(p) as zf:
        reserved = _reserved_names() if kind in ("module", "addon") else set()
        sources = {n: zf.read(n).decode("utf-8", "ignore") for n in zf.namelist() if n.endswith(".py")}
        scan = scan_sources(sources, reserved_tool_names=reserved, declared_binaries=meta.get("external_binaries"))
    if scan["verdict"] == "critical" and not allow_unsafe:
        return {
            "error": "safety scan: CRITICAL findings — refused (re-run with --allow-unsafe to override)",
            "scan": scan,
        }

    # wizard
    con.print(
        f"[dim]file sha256: {hashlib.sha256(p.read_bytes()).hexdigest()}[/dim]  [dim](compare against the hash the author published)[/dim]"
    )
    _render_card(con, meta, scan, allow_unsafe)
    if not yes:
        if not con.is_terminal:
            return {"error": "non-interactive session — confirmation requires --yes"}
        try:
            answer = con.input("[bold cyan]Install this package? [Y/n][/bold cyan] ").strip().lower()
        except KeyboardInterrupt:
            return {"error": "declined by operator"}
        except EOFError:
            answer = ""
        # ENTER installs (default Y — the wizard already showed the full
        # card; pressing Enter means 'looks good'. n/no declines.)
        if answer in ("n", "no"):
            return {"error": "declined by operator"}

    # destination
    if kind == "addon":
        from suijin.modules.addons.entry import addon_roots

        dest = Path(addon_roots()[0]) / pkg_id
    else:
        from suijin.modules.loader import PACK_ROOTS

        dest = Path(PACK_ROOTS[1]) / pkg_id  # user-space modules home
    if dest.exists():
        return {"error": f"{dest} already exists — uninstall first (suijin module uninstall {pkg_id})"}

    # extract (seal already verified paths)
    with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(p) as zf:
        zf.extractall(td)
        shutil.copytree(td, dest, dirs_exist_ok=False)

    # post-validate: broken pack => auto-uninstall
    if kind == "module":
        try:
            from suijin.modules.tools.lib.module_sdk import validate_module

            ok, problems = validate_module(pkg_id, root=dest.parent)  # THIS install's root
            if not ok:
                shutil.rmtree(dest, ignore_errors=True)
                return {"error": "pack failed on-disk validation: " + "; ".join(problems)}
        except Exception as e:  # noqa: BLE001 — validation env issues are data
            con.print(f"[yellow]post-validation skipped: {e}[/yellow]")

    _bump("installed", pkg_id)
    return {"installed": pkg_id, "kind": kind, "dest": str(dest), "scan": scan}


def _render_card(con, meta: dict, scan: dict, allow_unsafe: bool) -> None:
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from suijin.modules.platform.lib.safety.scan import render_findings

    con.print(
        Panel(
            Text(f"{meta.get('name')} v{meta.get('version')}  —  .{meta.get('kind')}", style="bold white"),
            title=" sj package ",
            title_align="left",
        )
    )
    con.print(f"[green]made by[/green] {meta.get('author')}  [dim]on {meta.get('built_at')}[/dim]\n")
    if meta.get("dev_note"):
        con.print(Markdown(str(meta["dev_note"])))
        con.print()
    if meta.get("description"):
        con.print(f"[dim]{meta['description'][:200]}[/dim]\n")

    verdict_style = {"clean": "green", "warnings": "yellow", "critical": "bold red"}.get(scan["verdict"], "white")
    con.print(f"safety scan: [{verdict_style}]{scan['verdict']}[/] ({scan['scanned']} file(s))")
    if scan["findings"]:
        con.print(render_findings(scan["findings"]))
    if scan["verdict"] == "critical" and allow_unsafe:
        con.print("[bold red]CRITICAL findings overridden with --allow-unsafe — installing anyway[/bold red]")
    con.print()

    if meta.get("tools"):
        t = Table(box=None, padding=(0, 1))
        t.add_column("tool", style="cyan")
        t.add_column("args", style="dim")
        t.add_column("description")
        for tool in meta["tools"][:15]:
            t.add_row(tool["name"], ", ".join(tool.get("args", [])), tool.get("description", ""))
        con.print(t)
    if meta.get("external_binaries"):
        con.print(f"\n[yellow]external binaries:[/] {', '.join(meta['external_binaries'])}")
    con.print("\n[dim]this package runs code with your permissions — review before installing[/dim]")
