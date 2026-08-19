import contextlib
import io
import shlex
import subprocess
from pathlib import Path


def python_eval(code: str = "") -> str:
    if not code.strip():
        return "Error: code required"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            try:
                result = eval(code, {"__builtins__": __builtins__}, {})
                if result is not None:
                    print(repr(result))
            except SyntaxError:
                exec(code, {"__builtins__": __builtins__}, {})
    except Exception as e:  # noqa: BLE001 — agent code errors are data
        return f"Error: {type(e).__name__}: {e}"
    return buf.getvalue().rstrip() or "(no output)"


def script_run(path: str = "", args: str = "") -> str:
    if not path:
        return "Error: path required"
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR, resolve_workspace_path

    p = Path(path)
    script = p if p.is_absolute() else resolve_workspace_path(str(p))
    if not script.is_file():
        return f"Error: {script} not found (write it to scripts/ first)"
    if script.suffix == ".py":
        argv = ["python3", str(script)] + shlex.split(args or "")
    else:
        argv = ["bash", str(script)] + shlex.split(args or "")
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300, cwd=str(WORKSPACE_DIR))
    except subprocess.TimeoutExpired:
        return "Error: timed out after 300s"
    out = (r.stdout or "") + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
    return f"exit={r.returncode}\n{out[:10000] or '(no output)'}"
