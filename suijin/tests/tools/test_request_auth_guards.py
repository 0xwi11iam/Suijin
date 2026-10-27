"""request_auth guards + resume-seed hygiene (2026-10-27).

Field burn: a resumed engagement's agent couldn't see request_auth in
its (stale, seeded) tool list, guessed arguments, and popped a browser
at https://localhost/login — window flashed and died. These pin the
three guards: early refusal of absurd login URLs, actionable failure
text, and stale system prompts never re-entering the graph.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch, tmp_path):
    """No test may ever pop a real browser window."""
    import suijin.modules.platform.lib.workspace as ws
    import suijin.server.tools.lib.browser_auth as ba

    monkeypatch.setattr(ba, "browser_capture", lambda *a, **k: {"success": False, "error": "playwright not installed"})
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._reset_engagement()
    return ba


class TestLoginUrlSanity:
    def test_local_login_url_for_remote_target_refused_early(self):
        from suijin.server.tools.lib.request_auth import request_auth

        out = request_auth(
            target="idp.example.com",
            hint="idp sign-in",
            login_url="https://localhost/login",
        )
        assert out.startswith("AUTH CONFIG ERROR")
        assert "idp.example.com/login" in out
        assert "Page.goto" not in out  # refused before any browser attempt

    def test_local_target_with_local_login_allowed(self):
        """The mock auth target runs on localhost — local+local is legit."""
        from suijin.server.tools.lib.request_auth import request_auth

        out = request_auth(target="localhost:9876", hint="mock target", login_url="http://localhost:9876/login")
        assert "AUTH CONFIG ERROR" not in out

    def test_remote_login_url_for_remote_target_passes(self):
        from suijin.server.tools.lib.request_auth import request_auth

        out = request_auth(target="idp.example.com", login_url="https://idp.example.com/login")
        assert "AUTH CONFIG ERROR" not in out


class TestBrowserFailureGuidance:
    def test_failed_capture_names_the_fix(self, monkeypatch):
        import suijin.server.tools.lib.browser_auth as ba

        monkeypatch.setattr(
            ba,
            "browser_capture",
            lambda *a, **k: {
                "success": False,
                "error": "Page.goto: net::ERR_CONNECTION_REFUSED at https://localhost/login",
            },
        )
        from suijin.server.tools.lib.request_auth import request_auth

        out = request_auth(target="idp.example.com", login_url="https://localhost/login")
        # pre-flight catches it before the browser — but if any dead URL
        # slips through to the capture, the failure must coach the fix
        if out.startswith("AUTH FAILED"):
            assert "wrong login_url" in out.lower() or "WRONG login_url" in out


class TestResumeSeedHygiene:
    def test_stale_system_prompts_dropped(self):
        from suijin.server.redteam.lib.redteamer import resume_seed_messages

        msgs = [
            {"role": "system", "content": "OLD tool catalog — no request_auth"},
            {"role": "user", "content": "objective"},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "system", "content": "another stale system block"},
        ]
        seeded = resume_seed_messages(msgs)
        assert len(seeded) == 2
        assert all(m["role"] != "system" for m in seeded)

    def test_limit_and_schema_guards(self):
        from suijin.server.redteam.lib.redteamer import resume_seed_messages

        msgs = [{"role": "user", "content": f"m{i}"} for i in range(250)]
        msgs.append({"role": "assistant", "content": None})  # bad schema — dropped
        msgs.append("not a dict")  # dropped
        seeded = resume_seed_messages(msgs)
        assert len(seeded) == 200
        assert seeded[-1]["content"] == "m249"

    def test_none_messages_safe(self):
        from suijin.server.redteam.lib.redteamer import resume_seed_messages

        assert resume_seed_messages(None) == []
