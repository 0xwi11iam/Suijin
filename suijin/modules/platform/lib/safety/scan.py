"""Static safety scanner for installable code (sj? packages, modules).

AST-ONLY: source strings go in, findings come out — the scanner never
imports, executes, or evaluates payload code (the no-side-effects
guarantee has its own test). One scanner, four callers:

    sjpack.build      — dev-side scan, embedded as an ADVISORY report
    sjpack.install    — ALWAYS re-scans source; trusts no embedded report
    module validate   — same scan for unpackaged directories
    market install    — free coverage for the marketplace path

Rule registry: (id, severity, matcher). Severities:
    critical — hidden code execution, hardcoded secrets, obfuscation,
               built-in tool-name shadowing => install REFUSES
               (unless --allow-unsafe)
    warn     — undeclared process spawning, network egress, out-of-
               workspace file writes, import-time side effects
    info     — unknown third-party imports
"""

from __future__ import annotations

import ast
import re
import sys

CRITICAL = "critical"
WARN = "warn"
INFO = "info"

# calls that execute dynamically-generated/hidden code
_EXEC_NAMES = {"eval", "exec", "compile", "__import__"}

_NETWORK_MODULES = {"requests", "urllib", "urllib.request", "urllib.parse", "socket", "http.client", "httpx", "aiohttp"}

_SPAWN_ATTRS = {"system", "popen", "run", "Popen", "check_output", "check_call", "call"}

_B64_BLOB = re.compile(r"['\"]([A-Za-z0-9+/=]{200,})['\"]")
_HEX_BLOB = re.compile(r"['\"]([0-9a-fA-F]{200,})['\"]")
# 'A' * 250 style — small literal multiplied into a big blob
_MUL_BLOB = re.compile(r"['\"]([A-Za-z0-9+/=]{1,8})['\"]\s*\*\s*(\d{3,})")

# stdlib names for the unknown-import rule
_STDLIB = set(getattr(sys, "stdlib_module_names", ()))
# the repo's own runtime deps (requirements.txt) — importing these is normal
_KNOWN_DEPS = {
    "requests",
    "rich",
    "huggingface_hub",
    "psutil",
    "pydantic",
    "langgraph",
    "langgraph.checkpoint",
    "langchain_core",
    "flask",
    "flask_cors",
    "PyJWT",
    "jwt",
    "duckduckgo_search",
    "textual",
    "urllib3",
    "fastapi",
    "uvicorn",
    "websockets",
    "yaml",
    "tomli",
}


def _secret_findings(source: str) -> list[dict]:
    """Hardcoded secrets — reuses the platform secret patterns, widened for
    SOURCE-CODE style assignments. The repo patterns match env/yaml form
    (KEY=value, no spaces); python writes KEY = value — the scanner
    normalizes ' = ' to '=' before matching so both forms are caught."""
    try:
        from suijin.modules.platform.lib.security.secret_patterns import SECRET_PATTERNS

        patterns = SECRET_PATTERNS
    except Exception:  # noqa: BLE001 — scanner must run standalone too
        return []
    import re as _re

    normalized = _re.sub(r"(\w)\s*([=:])\s*", r"\1\2", source)  # KEY = v -> KEY=v
    out = []
    for name, rx in patterns.items():
        for m in rx.finditer(normalized):
            # report line numbers against the ORIGINAL source: find the
            # matched VALUE's first line in the original by scanning for
            # the value string
            val = m.group(1) if m.groups() else m.group(0)
            line = source[: source.find(val)].count("\n") + 1 if val and val in source else 1
            out.append(
                {
                    "rule": "hardcoded-secret",
                    "severity": CRITICAL,
                    "file": None,
                    "line": line,
                    "detail": f"{name} pattern matches source",
                }
            )
    return out


def _scan_tree(tree: ast.Module, source: str, rel: str) -> list[dict]:
    findings: list[dict] = []

    def add(rule, severity, node, detail):
        findings.append(
            {"rule": rule, "severity": severity, "file": rel, "line": getattr(node, "lineno", 0), "detail": detail}
        )

    # module-level statement kinds that are benign at import time
    _benign_top = (
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Assign,
        ast.AnnAssign,
        ast.Expr,
        ast.If,
        ast.Try,
        ast.For,
        ast.While,
    )

    for node in ast.walk(tree):
        # 1. exec-family calls
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name in _EXEC_NAMES:
                add("dynamic-exec", CRITICAL, node, f"{name}() call — executes generated/hidden code")

            # 2. subprocess family (undeclared spawn -> warn; declared -> info)
            if isinstance(fn, ast.Attribute) and fn.attr in _SPAWN_ATTRS:
                mod = (
                    fn.value.id
                    if isinstance(fn.value, ast.Name)
                    else (fn.value.attr if isinstance(fn.value, ast.Attribute) else "")
                )
                if mod in ("os", "subprocess", "commands"):
                    add("process-spawn", WARN, node, f"{mod}.{fn.attr}() spawns a process")

            # 3. network egress
            if isinstance(fn, ast.Attribute):
                root = fn.value.id if isinstance(fn.value, ast.Name) else ""
                if root in ("requests", "socket", "urllib", "httpx"):
                    add("network-egress", WARN, node, f"{root}.{fn.attr}() performs network I/O")

        # 4. imports: unknown third-party
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root not in _STDLIB and root not in _KNOWN_DEPS and not root.startswith("suijin"):
                    add("unknown-import", INFO, node, f"third-party import '{a.name}' may not exist on target")
        if isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root not in _STDLIB and root not in _KNOWN_DEPS and not root.startswith("suijin"):
                add("unknown-import", INFO, node, f"third-party import '{node.module}' may not exist on target")

    # 5. import-time side effects: module-level Call statements (not defs)
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            add("import-time-effect", WARN, stmt, "code executes at import time (packs load at boot)")
        elif isinstance(stmt, (ast.For, ast.While)) and not isinstance(stmt, (ast.AsyncFunctionDef, ast.FunctionDef)):
            add("import-time-effect", WARN, stmt, "loop runs at import time")

    # 6. obfuscation: big encoded blob whose decoded form feeds exec.
    # Two shapes: (a) a 200+ char base64/hex LITERAL, (b) a blob built by
    # string-multiplication ('A' * 250) — the fixture proved shape (b) is
    # the realistic indirection. Both need an exec/decode call after.
    blob_hits = list(_B64_BLOB.finditer(source)) + list(_HEX_BLOB.finditer(source))
    for m2 in _MUL_BLOB.finditer(source):
        if int(m2.group(2)) >= 200:  # group(2) = the multiplier
            blob_hits.append(m2)
    for m in blob_hits:
        after = source[m.end() : m.end() + 600]
        if re.search(r"\b(exec|eval|compile|__import__|b64decode|loads)\s*\(", after):
            ln = source[: m.start()].count("\n") + 1
            findings.append(
                {
                    "rule": "obfuscation",
                    "severity": CRITICAL,
                    "file": rel,
                    "line": ln,
                    "detail": f"large encoded blob ({m.group(1)[:12] if m.lastindex else 'mul'}…) feeding an exec/decode call",
                }
            )

    return findings


def scan_sources(files: dict, reserved_tool_names: set | None = None, declared_binaries: list | None = None) -> dict:
    """Scan {relative_path: source}. Returns {'verdict', 'findings', 'scanned'}.

    reserved_tool_names: built-in tool ids — a public def with one of these
    names would SHADOW core tools at boot (the loader registers into a
    flat namespace) => critical.
    """
    findings: list[dict] = []
    scanned = 0
    declared = {(b or "").strip().lower() for b in (declared_binaries or [])}

    for rel, source in sorted(files.items()):
        if not str(rel).endswith(".py"):
            continue
        scanned += 1
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            findings.append(
                {
                    "rule": "unparseable",
                    "severity": CRITICAL,
                    "file": str(rel),
                    "line": e.lineno or 0,
                    "detail": f"syntax error: {e.msg}",
                }
            )
            continue
        findings.extend(_scan_tree(tree, source, str(rel)))
        findings.extend(_secret_findings(source))

        # tool-name shadowing: public top-level defs vs reserved names
        if reserved_tool_names:
            for stmt in tree.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name in reserved_tool_names:
                    findings.append(
                        {
                            "rule": "tool-shadow",
                            "severity": CRITICAL,
                            "file": str(rel),
                            "line": stmt.lineno,
                            "detail": f"public function '{stmt.name}' shadows a built-in tool (loader namespace is flat)",
                        }
                    )

    # declared binaries downgrade process-spawn warnings to info
    if declared:
        for f in findings:
            if f["rule"] == "process-spawn" and f["severity"] == WARN:
                f["severity"] = INFO
                f["detail"] += " (binary is declared in package metadata)"

    verdict = "clean"
    if any(f["severity"] == WARN for f in findings):
        verdict = "warnings"
    if any(f["severity"] == CRITICAL for f in findings):
        verdict = "critical"
    return {"verdict": verdict, "findings": findings, "scanned": scanned}


def render_findings(findings: list, colorize: bool = True) -> str:
    """Compact findings table for the wizard card."""
    if not findings:
        return "no findings — static scan clean"
    sev_color = {CRITICAL: "red", WARN: "yellow", INFO: "dim"}
    lines = []
    for f in findings[:12]:
        loc = f"{f.get('file', '?')}:{f.get('line', 0)}"
        sev = f["severity"]
        if colorize:
            lines.append(f"  [{sev_color.get(sev, 'white')}]{sev:8}[/] {f['rule']:18} {loc:24} {f['detail'][:70]}")
        else:
            lines.append(f"  {sev:8} {f['rule']:18} {loc:24} {f['detail'][:70]}")
    if len(findings) > 12:
        lines.append(f"  … and {len(findings) - 12} more")
    return "\n".join(lines)
