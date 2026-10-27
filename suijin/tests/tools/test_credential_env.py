"""Stored credentials export as $SUIJIN_CRED_<NAME> env vars (2026-10-27).

The agent references the VARIABLE in commands; execute_terminal injects
the value. The literal secret must never appear in command text — and
creds_list must not leak it into transcripts either.
"""

import pytest


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    import suijin.modules.credential_store.main as cs
    import suijin.modules.platform.lib.workspace as ws

    monkeypatch.setattr(cs, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(cs, "STORE_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._reset_engagement()  # hermetic: no engagement pinned from another test
    return cs


SECRET = "synthetic-PLACEHOLDER-credential-never-real"


class TestEnvNaming:
    def test_sanitizes_service_names(self, _store):
        assert _store.env_name_for("api.example.com") == "SUIJIN_CRED_API_EXAMPLE_COM"
        assert _store.env_name_for("my-service 2") == "SUIJIN_CRED_MY_SERVICE_2"
        assert _store.env_name_for("UPPER_already") == "SUIJIN_CRED_UPPER_ALREADY"

    def test_cred_env_exports_every_stored_credential(self, _store):
        _store.creds_add(service="api.example.com", cred_type="bearer", value=SECRET)
        assert _store.cred_env() == {"SUIJIN_CRED_API_EXAMPLE_COM": SECRET}

    def test_empty_and_blank_entries_skipped(self, _store):
        _store.creds_add(service="api.example.com", cred_type="bearer", value=SECRET)
        _store._save_store(
            {
                "_schema": "suijin-credentials-v1",
                "credentials": _store._load_store()["credentials"] + [{"service": "", "value": "  "}],
            }
        )
        assert _store.cred_env() == {"SUIJIN_CRED_API_EXAMPLE_COM": SECRET}


class TestGuidanceAndMasking:
    def test_creds_add_tells_agent_the_env_var(self, _store):
        out = _store.creds_add(service="api.example.com", cred_type="bearer", value=SECRET)
        assert "$SUIJIN_CRED_API_EXAMPLE_COM" in out

    def test_creds_list_masks_the_literal(self, _store):
        _store.creds_add(service="api.example.com", cred_type="bearer", value=SECRET)
        out = _store.creds_list()
        assert SECRET not in out  # the literal never reaches a transcript
        assert SECRET[:30] not in out
        assert "$SUIJIN_CRED_API_EXAMPLE_COM" in out


class TestTerminalInjection:
    def test_execute_terminal_resolves_cred_env_var(self, _store):
        """END-TO-END: the agent writes $SUIJIN_CRED_<NAME>; the real
        subprocess sees the value. The command text never contains it."""
        from suijin.server.tools.lib.terminal import execute_terminal

        _store.creds_add(service="api.example.com", cred_type="bearer", value=SECRET)
        out = execute_terminal('printf "GOT:%s" "$SUIJIN_CRED_API_EXAMPLE_COM"', timeout=20)
        assert f"GOT:{SECRET}" in out

    def test_command_text_never_holds_the_literal(self, _store):
        from suijin.server.tools.lib.terminal import execute_terminal

        _store.creds_add(service="api.example.com", cred_type="bearer", value=SECRET)
        # a leaked-var probe: shell echo of the var reference itself
        out = execute_terminal('echo "$SUIJIN_CRED_API_EXAMPLE_COM" | head -c 3', timeout=20)
        assert "syn" in out  # value flowed through
        # and the failing-var control: an unknown var stays empty
        out2 = execute_terminal('printf "EMPTY:[%s]" "$SUIJIN_CRED_NOT_STORED"', timeout=20)
        assert "EMPTY:[]" in out2
