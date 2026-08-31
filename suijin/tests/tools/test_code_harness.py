"""code_harness — the exploit dev loop: verdicts, mechanical fixes, sandbox."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.tools.lib.code_harness import code_harness  # noqa: E402


def test_pass_on_success_regex(tmp_path, monkeypatch):
    monkeypatch.setattr("suijin.modules.tools.lib.code_harness._sandbox_dir", lambda tag: tmp_path)
    out = code_harness(
        goal="proof test",
        language="python",
        code='print("FLAG{harness_works}")',
        success_regex=r"FLAG\{.*\}",
    )
    assert "VERDICT: PASS" in out
    assert "MATCH" in out


def test_fail_when_regex_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("suijin.modules.tools.lib.code_harness._sandbox_dir", lambda tag: tmp_path)
    out = code_harness(
        goal="no match",
        language="python",
        code='print("nothing")',
        success_regex=r"FLAG\{.*\}",
        max_cycles=1,
    )
    assert "VERDICT: FAIL" in out


def test_fail_regex_beats_success(tmp_path, monkeypatch):
    monkeypatch.setattr("suijin.modules.tools.lib.code_harness._sandbox_dir", lambda tag: tmp_path)
    out = code_harness(
        goal="fail guard",
        language="python",
        code='print("FLAG{x} ERROR DETECTED")',
        success_regex=r"FLAG",
        fail_regex=r"ERROR DETECTED",
        max_cycles=1,
    )
    assert "VERDICT: FAIL" in out


def test_timeout_kills(tmp_path, monkeypatch):
    monkeypatch.setattr("suijin.modules.tools.lib.code_harness._sandbox_dir", lambda tag: tmp_path)
    out = code_harness(
        goal="hang test",
        language="python",
        code="import time; time.sleep(60)",
        timeout_s=2,
        max_cycles=1,
    )
    assert "VERDICT: FAIL" in out
    assert "TIMEOUT" in out or "timeout" in out


def test_python_syntax_error_is_triaged_not_looped(tmp_path, monkeypatch):
    monkeypatch.setattr("suijin.modules.tools.lib.code_harness._sandbox_dir", lambda tag: tmp_path)
    out = code_harness(goal="syntax", language="python", code="def broken(:", max_cycles=3)
    assert "SyntaxError" in out
    assert "VERDICT: FAIL" in out


def test_bash_language(tmp_path, monkeypatch):
    monkeypatch.setattr("suijin.modules.tools.lib.code_harness._sandbox_dir", lambda tag: tmp_path)
    out = code_harness(
        goal="bash",
        language="bash",
        code='echo "PWNED-OK"',
        success_regex=r"PWNED-OK",
    )
    assert "VERDICT: PASS" in out


def test_custom_run_cmd(tmp_path, monkeypatch):
    monkeypatch.setattr("suijin.modules.tools.lib.code_harness._sandbox_dir", lambda tag: tmp_path)
    out = code_harness(
        goal="runcmd",
        language="python",
        code="x=1",
        run_cmd="echo RAN {file}",
        success_regex="RAN attempt",
        max_cycles=1,
    )
    assert "VERDICT: PASS" in out


def test_no_code_is_clean_error():
    out = code_harness(goal="empty", language="python", code="")
    assert out.startswith("Error:")


def test_sandbox_filename_traversal_neutralized(tmp_path, monkeypatch):
    written = {}

    class FakeDir(tmp_path.__class__):
        def __truediv__(self, other):
            written[str(other)] = True
            return tmp_path / str(other).replace("/", "_").replace("..", "_")

    monkeypatch.setattr("suijin.modules.tools.lib.code_harness._sandbox_dir", lambda tag: tmp_path)
    out = code_harness(goal="traverse", language="python", code="print(1)", filename="../../etc/passwd", max_cycles=1)
    assert "VERDICT:" in out  # ran safely
    assert not (tmp_path / "../../etc/passwd").exists()


def test_dispatch_routes_harness():
    from suijin.modules.tools.lib.dispatch import route_tool

    out = route_tool("code_harness", {"goal": "probe", "code": "print('HI')", "success_regex": "HI"}, {})
    assert "VERDICT: PASS" in str(out)
