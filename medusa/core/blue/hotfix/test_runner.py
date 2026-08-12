"""Test runner — verify patch doesn't break existing tests."""
import subprocess, os

def run_tests(codebase_path: str) -> dict:
    try:
        r = subprocess.run(["python3","-m","pytest","--tb=short"], cwd=codebase_path, capture_output=True, text=True, timeout=30)
        return {"passed": r.returncode == 0, "output": r.stdout[-1000:]}
    except Exception as e:
        import logging; logging.getLogger("medusa").warning(f"Test runner failed: {e}")
        return {"passed": False, "output": f"Test runner error: {e}"}
