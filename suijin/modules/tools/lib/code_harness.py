"""code_harness — the exploit-code dev loop: write → run → triage → fix → verdict.

One tool call = up to `max_cycles` execution cycles in a per-attempt
sandbox (outputs/engagements/<slug>/lab/h_<hash>/). Python is first-class:
SyntaxError/ModuleNotFoundError get mechanical fixes (auto pip-install)
before burning an LLM turn. Every language runs via `run_cmd` when given
(bash/php/go/js/...): same loop, exit-code + stdout/stderr triage.

The verdict is EVIDENCE: `record_finding` on a code-based exploit claim
is only honest with VERDICT: PASS — this closes the AI_CLAIMED hole.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess

_MAX_OUTPUT = 4000


def _sandbox_dir(tag: str) -> "object":
    from pathlib import Path

    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR, engagement_dir

    h = hashlib.sha1(str(tag).encode()).hexdigest()[:8]
    base = engagement_dir() if engagement_dir() else WORKSPACE_DIR
    d = Path(base) / "lab" / f"h_{h}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run(cmd: str, cwd, timeout_s: int) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            timeout=max(1, int(timeout_s)),
            capture_output=True,
            text=True,
            errors="ignore",
        )
        return p.returncode, (p.stdout or "")[:_MAX_OUTPUT], (p.stderr or "")[:_MAX_OUTPUT]
    except subprocess.TimeoutExpired:
        return 124, "", f"TIMEOUT after {timeout_s}s (harness killed the process)"
    except Exception as e:  # noqa: BLE001
        return 125, "", f"harness execution error: {e}"


def _triage_python(code: str, rc: int, out: str, err: str, sandbox) -> tuple[str, str]:
    """Mechanical fixes before an LLM turn: (action, note). action in
    {retry, pip, none}. `retry` re-runs (transient), `pip` installs the
    missing module then re-runs."""
    m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", err)
    if m:
        mod = m.group(1)
        pip_name = mod.split(".")[0].replace("_", "-")
        if pip_name in (
            "requests",
            "httpx",
            "pyjwt",
            "beautifulsoup4",
            "bs4",
            "aiohttp",
            "cryptography",
            "paramiko",
            "scapy",
        ):
            src = "bs4" if pip_name == "bs4" else pip_name
            rc2, o2, e2 = _run(f"{__import__('sys').executable} -m pip install -q {shlex.quote(src)}", sandbox, 120)
            if rc2 == 0:
                return "retry", f"auto-installed missing module '{mod}'"
            return "none", f"pip install {mod} failed: {e2[:200]}"
        return "pip", f"missing module '{mod}' — not auto-installable, fix the import or vendor it"
    if re.search(r"^SyntaxError", err, re.MULTILINE):
        return "none", "SyntaxError — code must be rewritten"
    return "none", ""


_LANG_DEFAULT_RUN = {
    "python": "python3 {file}",
    "bash": "bash {file}",
    "sh": "sh {file}",
    "php": "php {file}",
    "node": "node {file}",
    "js": "node {file}",
    "go": "go run {file}",
    "ruby": "ruby {file}",
}


def code_harness(
    goal: str = "",
    language: str = "python",
    code: str = "",
    run_cmd: str = "",
    success_regex: str = "",
    fail_regex: str = "",
    filename: str = "",
    timeout_s: int = 30,
    max_cycles: int = 3,
) -> str:
    """Run the dev loop. VERDICT: PASS only when success_regex matched
    (or exit 0 with no fail_regex when no success_regex given)."""
    try:
        lang = (language or "python").lower()
        if not code and not run_cmd:
            return "Error: code_harness needs 'code' (and optionally run_cmd)."
        ext = {
            "python": "py",
            "bash": "sh",
            "sh": "sh",
            "php": "php",
            "node": "js",
            "js": "js",
            "go": "go",
            "ruby": "rb",
        }.get(lang, "txt")
        fname = filename or f"attempt.{ext}"
        if "/" in fname or ".." in fname:
            fname = fname.replace("/", "_").replace("..", "_")
        sandbox = _sandbox_dir(goal or code[:40])
        from pathlib import Path

        (Path(sandbox) / fname).write_text(str(code), encoding="utf-8")
        cmd = run_cmd.replace("{file}", fname) or _LANG_DEFAULT_RUN.get(lang, "./{file}").replace("{file}", fname)

        sr = re.compile(success_regex) if success_regex else None
        fr = re.compile(fail_regex) if fail_regex else None
        log = [f"code_harness — {goal[:120]}", f"sandbox: {sandbox}", f"cmd: {cmd}", "-" * 60]
        verdict = "FAIL"
        cycles = max(1, min(int(max_cycles or 3), 6))
        for i in range(1, cycles + 1):
            rc, out, err = _run(cmd, sandbox, timeout_s)
            log.append(f"── cycle {i}/{cycles}  exit={rc}")
            if out:
                log.append(f"stdout[:600]: {out[:600]}")
            if err:
                log.append(f"stderr[:600]: {err[:600]}")
            if rc == 124:
                log.append("timeout — process killed")
                break
            matched = bool(sr.search(out + err)) if sr else (rc == 0)
            failed = bool(fr and fr.search(out + err))
            if matched and not failed:
                verdict = "PASS"
                log.append(f"MATCH: success{'_regex' if sr else ' criteria'} satisfied")
                break
            if i < cycles and lang == "python":
                action, note = _triage_python(code, rc, out, err, sandbox)
                if note:
                    log.append(f"triage: {note}")
                if action == "retry":
                    continue
            log.append("criteria not met")
        log.append("-" * 60)
        log.append(f"VERDICT: {verdict}")
        if verdict == "PASS":
            log.append("Evidence captured — quote this output in record_finding.")
        else:
            log.append("Fix the code and call code_harness again (same sandbox is reused).")
        return "\n".join(log)
    except Exception as e:  # noqa: BLE001 — tools return strings, never raise
        return f"Error: code_harness failed: {e}"
