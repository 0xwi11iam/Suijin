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
        try:
            _home = os.environ.get("SUIJIN_HOME") or os.path.join(os.path.expanduser("~"), ".suijin", "workspace")
            if "/tmp/" in cmd and _home:
                import re as _re_tmp

                # only bare /tmp/, not /var/tmp/ or $TMPDIR
                _resolved = _re_tmp.sub(r"(?<!var)/tmp/", f"{_home}/tmp/", cmd)
                if _resolved != cmd:
                    cmd = _resolved
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
                "engagement's artifact (cross-target key confusion has happened). Use "
                "$TMPDIR / your workspace home for scratch, and verify any /tmp-sourced "
                "secret actually appears on YOUR target before claiming it.\n"
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
        if _tmp_warn:
            out = _tmp_warn + out
        return truncate(out)
    except Exception as e:
        return f"Execution Fault: {str(e)}"
