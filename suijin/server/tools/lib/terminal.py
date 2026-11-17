"""Shell execution gateway for Suijin tools.

Scoped to the agent workspace with destructive-command guardrails.
"""

from __future__ import annotations

import os
import re
import shlex
import sys


def _ws():
    """Platform workspace accessors, resolved lazily (module boundary rule)."""
    from suijin.modules.platform.lib import workspace

    return workspace


from .result import run_command

# PERSISTENT CWD (2026-10-17): set when the model cd's, survives across
# execute_terminal calls for the engagement, reset by _reset_cwd() at
# engagement start. None = always the engagement home (old behavior).
_PERSIST_CWD: str | None = None


def _reset_cwd() -> None:
    global _PERSIST_CWD
    _PERSIST_CWD = None


def _extract_cd(cmd: str) -> str | None:
    """The path of a LEADING `cd <dir>` (the model's habit) — captured
    so the NEXT call starts there instead of forcing another cd prefix."""
    m = re.match(r"^\s*cd\s+([^;&|]+?)(?:\s*&&|\s*;|$)", cmd)
    if not m:
        return None
    d = m.group(1).strip().strip("\"'")
    if d in ("..", "-", "$OLDPWD") or d.startswith("$"):
        return None
    return d or None



# ── TERMINAL EGRESS GUARD (field 2026-11-17) ─────────────────────────
# The transport guard covers http_request, but agents also shell out to
# curl/wget/python-requests — which bypassed the socket rails entirely
# and reached out-of-scope hosts (canvas/nexus subdomains). This is the
# same scope allowlist + 1-request-per-2-seconds, enforced on the shell
# command string BEFORE anything runs.
_AUX_HOSTS = {
    "bugcrowd.com", "www.bugcrowd.com",
    "crt.sh", "certspotter.com", "api.certspotter.com",
    "github.com", "api.github.com", "raw.githubusercontent.com",
    "codeload.github.com", "objects.githubusercontent.com",
    "pypi.org", "files.pythonhosted.org",
    "localhost", "127.0.0.1", "::1",
}
_SCANNER_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(nmap|masscan|rustscan|naabu|ffuf|gobuster|dirb|dirsearch|"
    r"nikto|sqlmap|hydra|wfuzz|zap|nuclei|subfinder|amass|dnsx|shuffledns)"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s'\"<>()\[\]]+", re.IGNORECASE)
_LOOP_RE = re.compile(r"(^|[;&|]\s*)(for|while|xargs|parallel|seq)\b|\$\(", re.IGNORECASE)
_NET_RE = re.compile(r"curl|wget|https?://", re.IGNORECASE)
_SCRIPT_NET_RE = re.compile(r"\b(socket|urllib|requests|http\.client|aiohttp|httpx|fetch)\b")
_SCRIPT_LOOP_RE = re.compile(r"\b(ThreadPool|concurrent\.futures|asyncio\.gather|for\s+\w+\s+in|while\s+)")
_SCRIPT_RE = re.compile(r"(?:^|[;&|]\s*)(?:python3?|node|bash|sh|zsh)\s+(?:-c\s+)?([^\s;&|]+\.(?:py|js|sh))")


def _egress_guard(cmd: str) -> str | None:
    """None = allowed; a string = the refusal the agent sees."""
    import time
    from pathlib import Path as _P
    from urllib.parse import urlparse

    if not cmd:
        return None
    hit = _SCANNER_RE.search(cmd)
    if hit:
        return (
            f"BLOCKED: {hit.group(1)} is a bulk scanner — the program rules allow single, "
            "rate-limited requests only (no DoS, no bulk scanning). Use http_request or a "
            "single curl against one in-scope URL."
        )
    # loops / parallel / backgrounded fetchers are bulk by construction
    if _NET_RE.search(cmd) and (_LOOP_RE.search(cmd) or re.search(r"&\s*\)", cmd) or re.search(r"-P\s|--parallel|-Z\b", cmd)):
        return (
            "BLOCKED: network calls inside loops/background/parallel constructs are bulk traffic. "
            "Send ONE request per command, at most 1 per 2s per host."
        )
    # scripted network sweeps: inspect the referenced script before running it
    for script in _SCRIPT_RE.findall(cmd):
        candidates = []
        raw = script.strip("'\"")
        if raw.startswith("/"):
            candidates.append(_P(raw))
        else:
            candidates.extend([_P.cwd() / raw, _P.home() / raw])
        for cand in candidates:
            try:
                if cand.is_file():
                    body = cand.read_text(errors="replace")[:20000]
                    if _SCRIPT_NET_RE.search(body) and _SCRIPT_LOOP_RE.search(body):
                        return (
                            f"BLOCKED: {cand.name} performs scripted network iteration "
                            "(network client + loop/concurrency). Program rules forbid bulk "
                            "requests; rewrite as single, rate-limited requests."
                        )
            except Exception:  # noqa: BLE001
                continue
    # inline network iteration (python -c / node -e / shell)
    if _NET_RE.search(cmd) and _SCRIPT_NET_RE.search(cmd) and _SCRIPT_LOOP_RE.search(cmd):
        return (
            "BLOCKED: inline script combines a network client with a loop/concurrency — "
            "that is bulk traffic. Use one request per call."
        )
    try:
        from suijin.server.tools.lib.transport_guards import scope_patterns

        patterns = scope_patterns()
    except Exception:  # noqa: BLE001
        patterns = []
    if not patterns:
        return None  # no allowlist configured => no scope opinion here
    urls = _URL_RE.findall(cmd)
    for raw in urls:
        host = (urlparse(raw).hostname or "").lower()
        if not host:
            continue
        if host in _AUX_HOSTS or any(host.endswith("." + a) for a in _AUX_HOSTS if a[0].isalpha()):
            continue
        in_scope = False
        for pat in patterns:
            try:
                if re.match(pat, host):
                    in_scope = True
                    break
            except re.error:
                continue
        if not in_scope:
            return (
                f"BLOCKED: {host} is outside the engagement scope (terminal egress guard). "
                "Only the program's in-scope assets may be contacted; passive OSINT and "
                "public source clones are separate allowed paths."
            )
        # per-host pacing shared through the workspace (1 request / 2s)
        try:
            stamp_dir = _P.home() / ".suijin" / "tmp"
            stamp_dir.mkdir(parents=True, exist_ok=True)
            stamp = stamp_dir / ("egress-" + re.sub(r"[^a-z0-9.-]", "_", host) + ".stamp")
            now = time.time()
            if stamp.is_file():
                wait = 2.0 - (now - stamp.stat().st_mtime)
                if wait > 0:
                    time.sleep(min(wait, 2.0))
            stamp.write_text(str(now))
        except Exception:  # noqa: BLE001
            pass
    return None


def execute_terminal(cmd, timeout=30, cwd=None):
    """Shell execution gateway — scoped to suijin_agent/ workspace.

    Commands that modify the global system (pip install, brew, apt, sudo, etc.)
    are intercepted and require explicit user approval before execution.

    cwd: optional persistent working directory (the cwd-persistence
    feature — None runs from the engagement home as before).
    """
    try:
        if not cmd:
            return "Error: No command provided."
        # scope + rate + scanner guard, BEFORE the shell sees the string
        refusal = _egress_guard(str(cmd))
        if refusal:
            return refusal

        # CWD PERSISTENCE (2026-10-17): 62% of the google run's terminal
        # calls re-typed `cd /engagement/... && …` because each call
        # reset to the home. A module-level cwd survives between calls
        # (set when the model cd's, restored when the dir vanishes).
        global _PERSIST_CWD
        if cwd is None:
            cwd = _PERSIST_CWD
        # TEMP DIR AUTO-RESOLUTION (2026-10-18): /tmp/foo → engagement
        # home tmp/foo — the agent writes there naturally and the sandbox
        # used to warn + redirect + confuse the model for 3 iterations.
        # Only bare /tmp/ paths (NOT /var/tmp/ which the sandbox should
        # deny — the jail test depends on that denial).
        # 2026-10-29 ROOT-CAUSE FIX: the fallback default here used to be
        # the RETIRED ~/.suijin/workspace path — the harness itself taught
        # the model that path (54 uses in one trail, every one denied).
        # The engagement home from the workspace module is canonical now.
        try:
            _home = os.environ.get("SUIJIN_HOME") or str(_ws().home_dir())
            if "/tmp/" in cmd and _home:
                import re as _re_tmp

                # only bare /tmp/, not /var/tmp/ or $TMPDIR
                _resolved = _re_tmp.sub(r"(?<!var)/tmp/", f"{_home}/tmp/", cmd)
                if _resolved != cmd:
                    cmd = _resolved
        except Exception:
            pass

        # PATH HEALING (2026-10-29): retired workspace paths rewrite to
        # the engagement home; redirects-quoted-inside-paths get flagged.
        _heal_notes: list[str] = []
        try:
            from suijin.server.tools.lib.output_normalizer import heal_command

            cmd, _heal_notes = heal_command(cmd, _home if "/tmp/" in cmd or ".suijin" in cmd else str(_ws().home_dir()))
        except Exception:
            pass

        _new_cwd = _extract_cd(cmd)  # from the ORIGINAL text, before prefixing
        if _new_cwd:
            _PERSIST_CWD = _new_cwd
        if cwd:
            cmd = f'cd {shlex.quote(str(cwd))} 2>/dev/null || cd "$SUIJIN_HOME"; {cmd}'

        # Suijin does not run as native Python on Windows — the tool
        # ecosystem (packs, POSIX shell) is Linux-based. Windows users
        # run the container via install.ps1 / docker compose.
        if sys.platform == "win32":
            return (
                "Error: native Windows is not supported. Suijin runs on macOS, "
                "Linux, or via Docker on Windows — use install.ps1 to set up the "
                "container, then run commands inside it."
            )

        # Self-Kill Protection
        my_pid = str(os.getpid())
        cmd_tokens = cmd.replace(";", " ").replace("&&", " ").replace("|", " ").split()
        if "kill" in cmd_tokens and my_pid in cmd_tokens:
            return f"SYSTEM OVERRIDE: Refusing to execute command. {my_pid} is the AI Agent's own Process ID. You must find the target application's PID."

        # Global-action gate: intercept dangerous commands
        from suijin.modules.tools.lib.guardrails import is_dangerous

        dangerous, pattern = is_dangerous(cmd)
        from suijin.modules.tools.lib.guardrails import confirm_global_action

        if dangerous and not confirm_global_action(cmd, pattern):
            # BLOCK PSYCHOLOGY (2026-10-20, ARTEX guard/ adaptation): a
            # bare denial reads like a WAF/403 to a pentest agent — its
            # instinct is to bypass it. State plainly that this is the
            # OPERATOR'S platform, not the target's defense.
            return (
                "[PLATFORM CONTROL — this is NOT the target's defense. This block comes from your "
                "operator's platform governance; do not attempt to bypass it as you would a WAF. "
                "Adapt the technique within authorized scope instead.]\n"
                f"Command denied by platform guardrail (matched: {pattern}).\nCommand was: {cmd[:200]}"
            )
        # Approved (or not dangerous) — proceed with execution

        # SHARED-/tmp GUARD (2026-10-08, a three-target credential
        # confusion): TMPDIR confinement redirects tempfile-style writes,
        # but HARDCODED /tmp paths escape it — and /tmp is machine-shared
        # by every concurrent engagement, so a peer's downloaded bundle
        # reads as this engagement's finding. Warn in the observation the
        # agent reads next turn; it self-corrects.
        # 2026-10-20 COHERENCE: the bare-/tmp auto-rewrite above already
        # redirected `/tmp/foo` away; this warning now fires ONLY on
        # /tmp usage that SURVIVED rewriting, and NEVER on /var/tmp
        # (which the old substring check matched — a denied jail probe
        # was getting a 'shared /tmp' warning, two contradictory stories
        # about the same path in one output).
        _tmp_warn = ""
        import re as _re_w

        if _re_w.search(r"(?<!var)/tmp", cmd):
            _tmp_warn = (
                "\n[sandbox] WARNING: that command touches /tmp, which is SHARED by all "
                "concurrent engagements on this machine — a file there may be another "
                "engagement's artifact (cross-target key confusion has happened). Write "
                "scratch files to $TMPDIR (your engagement tmp/ — it exists) and verify any "
                "/tmp-sourced secret actually appears on YOUR target before claiming it.\n"
            )

        # Build environment with homebrew paths (macOS) on top of the
        # v2 executor confinement (HOME/TMPDIR always point INTO the
        # engagement home; sandbox-exec write-deny when enabled).
        from suijin.server.confinement import executor_env, wrap_exec

        env = executor_env()
        brew_paths = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/homebrew/sbin"]
        current_path = env.get("PATH", "")
        for bp in brew_paths:
            if bp not in current_path:
                current_path = f"{bp}:{current_path}"
        env["PATH"] = current_path

        # STORED CREDENTIALS AS ENV VARS (2026-10-27): every credential in
        # the store exports as $SUIJIN_CRED_<NAME> so command text
        # references the VARIABLE — the literal secret never appears in
        # the command line, transcript, or any artifact the agent writes.
        try:
            from suijin.modules.credential_store.main import cred_env

            env.update(cred_env())
        except Exception:  # noqa: BLE001 — a store hiccup must not kill the shell
            pass

        # CRITICAL (v5.2): commands containing shell metacharacters
        # (;|&><$`() must run through a shell — shlex.split succeeds on
        # them but the tokens are meaningless as argv, so "a; b" was
        # exec'd as a literal ";" argument. This broke every pipeline
        # and chained command in three documented field engagements.
        import re as _re

        needs_shell = bool(_re.search(r"[;|&><$`()]", cmd))
        if needs_shell:
            cmd_parts = ["/bin/sh", "-c", cmd]
        else:
            # Tokenize; fall back to a shell one-liner for quoted/compound commands
            try:
                cmd_parts = shlex.split(cmd)
            except ValueError:
                cmd_parts = ["/bin/sh", "-c", cmd]

        # Stealth (v5.1): loud tools get tool-level rate caps — same work,
        # same parallelism, just not machine-gun fast. Benign commands
        # and operator-throttled invocations pass through untouched.
        try:
            from suijin.modules.platform.lib.stealth import sanitize_command

            if len(cmd_parts) > 1 and cmd_parts[0] != "/bin/sh":
                cmd_parts = sanitize_command(cmd_parts)
        except Exception:  # noqa: BLE001 — never block execution
            pass
        # v2 executor jail: run the command under the OS process sandbox
        # (write-deny outside workspace) when it is installed & enabled.
        run_cmd, _jailed = wrap_exec(cmd_parts if len(cmd_parts) > 1 else ["/bin/sh", "-c", cmd])
        result = run_command(
            run_cmd,
            timeout=timeout,
            cwd=str(_ws().home_dir()),  # the agent's ~ (engagement home)
            env=env,
            command_text=cmd,
        )
        from suijin.modules.platform.lib.runtime import truncate

        out = result.format()
        # OUTPUT NORMALIZATION (2026-10-29): the model reads RESULTS, not
        # noise — ffuf usage dumps collapse to a diagnosis, -o json parses
        # to a table, progress spam strips, giant JSON extracts to a
        # summary. Field evidence: 4 help dumps + 15/52 truncated steps.
        try:
            from suijin.server.tools.lib.output_normalizer import normalize_terminal_output

            out = normalize_terminal_output(cmd, out)

            # LARGE OUTPUT → FILE (wall memo 1e): the full text lands in
            # outputs/<ts>.txt and the result names it. The model can read
            # what it just did — 'output is cut at 8000 characters, the
            # rest is not in a file' was the lie.
            try:
                if len(out) > 12_000:
                    import re as _re_ts

                    _ts = _re_ts.sub(r"[^\w]", "-", cmd[:24]).strip("-") or "output"
                    import time as _time_mod

                    _f = _ws().home_dir() / "outputs" / f"{int(_time_mod.monotonic() * 1000):.0f}-{_ts}.txt"
                    _f.parent.mkdir(parents=True, exist_ok=True)
                    _f.write_text(out, encoding="utf-8")
                    head = out[:4000]
                    tail = out[-2000:]
                    out = f"{head}\n\n[… {len(out):,} chars total — FULL OUTPUT SAVED: {_f} — read_file it]\n\n{tail}"
            except Exception:  # noqa: BLE001 — the file never blocks the result
                pass

            # ALTERNATES (wall memo 1c): a not-installed error carries
            # the exact command that works right now — the model's first
            # failure teaches instead of dead-ending.
            try:
                from suijin.modules.tools.lib.alternates import annotate_missing_binary

                out = annotate_missing_binary(out)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001 — normalization never kills a result
            pass
        for _note in _heal_notes:
            out = _note + "\n" + out
        if _tmp_warn:
            out = _tmp_warn + out
        return truncate(out)
    except Exception as e:
        return f"Execution Fault: {str(e)}"
