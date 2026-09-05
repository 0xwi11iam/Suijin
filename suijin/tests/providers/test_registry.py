"""Registry provider tests — the OpenAI-compatible engine against a fake
server, key gating, custom: LAN resolution, and spec integrity."""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.providers.lib.registry import (  # noqa: E402
    CLOUD_KEYS,
    LOCAL_KEYS,
    PROVIDER_REGISTRY,
    ProviderSpec,
    resolve_custom_provider,
)


class _FakeCompatHandler(BaseHTTPRequestHandler):
    """Mimics an OpenAI-compatible /chat/completions: SSE when stream=true,
    plain JSON otherwise. 401 when Authorization is wrong."""

    server_version = "FakeCompat/1.0"

    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        auth = self.headers.get("Authorization", "")
        if self.server.require_key and auth != f"Bearer {self.server.api_key}":
            self._send(401, {"error": {"message": "bad key"}})
            return
        if self.server.fail_with:
            self._send(self.server.fail_with, {"error": {"message": "nope"}})
            return
        model = body.get("model", "")
        if self.server.missing_models and model in self.server.missing_models:
            self._send(404, {"error": {"message": f"model {model} not found"}})
            return
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"choices": [{"delta": {"content": "hel"}}]},
                {"choices": [{"delta": {"content": "lo"}}]},
                {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
            ]
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self._send(
                200,
                {
                    "choices": [{"message": {"content": "hello"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 2},
                },
            )

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def fake_server(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _FakeCompatHandler)
    srv.require_key = True
    srv.api_key = "test-key-123"
    srv.fail_with = 0
    srv.missing_models = set()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def _spec_on_fake(base_url, **kw):
    return ProviderSpec(
        key="fakeprov",
        label="FakeProv",
        base_url=base_url,
        key_envs=("FAKEPROV_API_KEY",),
        default_model="fake-1",
        pricing=(1.0, 2.0),
        **kw,
    )


class TestRegistryIntegrity:
    def test_unique_keys_and_families(self):
        assert len(PROVIDER_REGISTRY) >= 18
        cloud = {k for k in CLOUD_KEYS if k in PROVIDER_REGISTRY}
        local = {k for k in LOCAL_KEYS if k in PROVIDER_REGISTRY}
        assert len(cloud) >= 12
        assert len(local) >= 4

    def test_cloud_specs_keyed_locals_keyless(self):
        for k in CLOUD_KEYS:
            s = PROVIDER_REGISTRY[k]
            assert s.key_envs, f"{k} (cloud) must declare key_envs"
            assert not s.local
            assert s.base_url.startswith("https://")
        for k in LOCAL_KEYS:
            s = PROVIDER_REGISTRY[k]
            assert not s.key_envs, f"{k} (local) must be keyless"
            assert s.local
            assert s.base_url.startswith("http://")

    def test_env_map_covers_registry(self):
        from suijin.modules.providers.lib.registry import KEY_ENV_BY_PROVIDER

        for k in CLOUD_KEYS:
            assert KEY_ENV_BY_PROVIDER[k].endswith("_API_KEY") or KEY_ENV_BY_PROVIDER[k] == "HF_TOKEN"


class TestCompatEngine:
    def test_streaming_success(self, fake_server, monkeypatch):
        import suijin.modules.providers.lib.registry as reg
        from suijin.modules.providers.lib import generate

        monkeypatch.setenv("FAKEPROV_API_KEY", "test-key-123")
        spec = _spec_on_fake(f"http://127.0.0.1:{fake_server.server_port}/v1")
        monkeypatch.setitem(reg.PROVIDER_REGISTRY, "fakeprov", spec)
        out = generate([{"role": "user", "content": "hi"}], {"provider": "fakeprov"})
        assert out == "hello"

    def test_missing_key_clean_error(self, monkeypatch):
        import suijin.modules.providers.lib.registry as reg
        from suijin.modules.providers.lib import generate

        monkeypatch.delenv("FAKEPROV_API_KEY", raising=False)
        spec = _spec_on_fake("https://fake.example/v1")
        monkeypatch.setitem(reg.PROVIDER_REGISTRY, "fakeprov", spec)
        out = generate([{"role": "user", "content": "hi"}], {"provider": "fakeprov"})
        assert out.startswith("Error:")
        assert "FAKEPROV_API_KEY" in out

    def test_invalid_key_401(self, fake_server, monkeypatch):
        import suijin.modules.providers.lib.registry as reg
        from suijin.modules.providers.lib import generate

        monkeypatch.setenv("FAKEPROV_API_KEY", "wrong-key")
        spec = _spec_on_fake(f"http://127.0.0.1:{fake_server.server_port}/v1")
        monkeypatch.setitem(reg.PROVIDER_REGISTRY, "fakeprov", spec)
        out = generate([{"role": "user", "content": "hi"}], {"provider": "fakeprov"}, retries=1)
        assert out.startswith("Error:")
        assert "401" in out or "Invalid" in out

    def test_local_keyless_and_model_from_config(self, fake_server, monkeypatch):
        import suijin.modules.providers.lib.registry as reg
        from suijin.modules.providers.lib import generate

        spec = ProviderSpec(
            key="fakelocal",
            label="FakeLocal",
            base_url=f"http://127.0.0.1:{fake_server.server_port}/v1",
            key_envs=(),
            default_model="",
            pricing=None,
            local=True,
        )
        fake_server.require_key = False
        monkeypatch.setitem(reg.PROVIDER_REGISTRY, "fakelocal", spec)
        out = generate([{"role": "user", "content": "hi"}], {"provider": "fakelocal", "fakelocal_model": "fake-1"})
        assert out == "hello"

    def test_no_model_guidance(self, monkeypatch):
        import suijin.modules.providers.lib.registry as reg
        from suijin.modules.providers.lib import generate

        spec = ProviderSpec(key="bare", label="Bare", base_url="http://127.0.0.1:1/v1", key_envs=(), default_model="")
        monkeypatch.setitem(reg.PROVIDER_REGISTRY, "bare", spec)
        out = generate([{"role": "user", "content": "hi"}], {"provider": "bare"})
        assert out.startswith("Error:")
        assert "bare_model" in out


class TestCustomProviders:
    def test_resolve_from_config(self):
        cfg = {
            "custom_providers": [
                {"name": "labbox", "base_url": "http://10.0.0.5:8000/v1", "model": "qwen3-coder:30b"},
                {"name": "remote", "base_url": "https://llm.example.com/v1", "api_key": "sk-x"},
            ]
        }
        lab = resolve_custom_provider("labbox", cfg)
        assert lab is not None and lab.local and lab.default_model == "qwen3-coder:30b"
        remote = resolve_custom_provider("remote", cfg)
        assert remote is not None and not remote.local and remote.inline_key == "sk-x"
        assert resolve_custom_provider("nope", cfg) is None

    def test_generate_routes_custom(self, fake_server, monkeypatch):
        from suijin.modules.providers.lib import generate

        fake_server.require_key = False
        cfg = {
            "provider": "custom:mybox",
            "custom_providers": [
                {"name": "mybox", "base_url": f"http://127.0.0.1:{fake_server.server_port}/v1", "model": "fake-1"}
            ],
        }
        out = generate([{"role": "user", "content": "hi"}], cfg)
        assert out == "hello"

    def test_unknown_custom_missing_entry(self):
        from suijin.modules.providers.lib import generate

        out = generate([{"role": "user", "content": "hi"}], {"provider": "custom:ghost"})
        assert out.startswith("Error:")


class TestUnrestrictedCustomProvider:
    """`suijin custom` / wizard option 9: user enters base URL + API key.
    The key is ANY string — no format policing (gateways, proxies and
    self-hosted boxes use arbitrary tokens; blank = keyless)."""

    def test_weird_keys_and_urls_pass_verbatim(self):
        # keys: unicode, symbols, spaces, colons — anything
        for weird in ("!!! what a key 🐟", "sk-not-a-real-format", "a:b:c", "x" * 200, ""):
            cfg = {"custom_providers": [{"name": "gw", "base_url": "https://gw.example/api", "api_key": weird}]}
            spec = resolve_custom_provider("gw", cfg)
            assert spec is not None and spec.inline_key == weird
            assert spec.key_envs == () and not spec.requires_key  # never key-gated
        # URLs: any scheme/host/port/path, trailing slash tolerated
        for url in ("http://10.0.0.5:8000/v1", "https://api.internal.corp/gateway/openai/v1/", "http://[::1]:1234"):
            cfg = {"custom_providers": [{"name": "n", "base_url": url}]}
            spec = resolve_custom_provider("n", cfg)
            assert spec is not None and spec.base_url == url.rstrip("/")

    def test_end_to_end_weird_key_authenticates(self, fake_server):
        import suijin.modules.providers.lib as pl

        weird = "!!! my gateway key $$"  # symbols + spaces — exact match required
        fake_server.require_key = True
        fake_server.api_key = weird
        cfg = {
            "provider": "custom:gw",
            "custom_providers": [
                {
                    "name": "gw",
                    "base_url": f"http://127.0.0.1:{fake_server.server_port}",
                    "api_key": weird,
                    "model": "m-1",
                }
            ],
        }
        out = pl.generate([{"role": "user", "content": "hi"}], cfg, max_tokens=8, retries=1)
        assert not str(out).startswith("Error:") and "hello" in str(out)

    def test_non_latin1_key_never_crashes_the_transport(self, fake_server):
        """A key with characters HTTP headers can't carry (emoji, CJK) flies
        percent-encoded instead of dying with UnicodeEncodeError."""
        from urllib.parse import quote

        import suijin.modules.providers.lib as pl

        emoji_key = "k🐟_PASS"
        fake_server.require_key = True
        fake_server.api_key = quote(emoji_key, safe="")  # what arrives on the wire
        cfg = {
            "provider": "custom:gw",
            "custom_providers": [
                {
                    "name": "gw",
                    "base_url": f"http://127.0.0.1:{fake_server.server_port}",
                    "api_key": emoji_key,
                    "model": "m-1",
                }
            ],
        }
        out = pl.generate([{"role": "user", "content": "hi"}], cfg, max_tokens=8, retries=1)
        assert not str(out).startswith("Error:") and "hello" in str(out)

    def test_keyless_custom_works(self, fake_server):
        import suijin.modules.providers.lib as pl

        fake_server.require_key = False
        cfg = {
            "provider": "custom:bare",
            "custom_providers": [
                {
                    "name": "bare",
                    "base_url": f"http://127.0.0.1:{fake_server.server_port}",
                    "api_key": "",
                    "model": "m-1",
                }
            ],
        }
        out = pl.generate([{"role": "user", "content": "hi"}], cfg, max_tokens=8, retries=1)
        assert not str(out).startswith("Error:")


class TestCustomProviderSetup:
    """The interactive adder behind `suijin custom` and wizard option 9."""

    def test_add_custom_provider_writes_config(self, tmp_path, monkeypatch):
        import suijin.modules.platform.lib.config_loader as cl

        monkeypatch.setattr(cl, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(cl, "ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        answers = iter(
            [
                "https://gw.example/v1",  # base URL
                "!!! my gateway key 🐟",  # api key — anything
                "llama4-maverick",  # model
                "gateway",  # name
            ]
        )
        monkeypatch.setattr("builtins.input", lambda *a: next(answers))
        assert cl.add_custom_provider() is True
        cfg = json.loads((tmp_path / "config.json").read_text())
        assert cfg["provider"] == "custom:gateway"
        assert cfg["custom_providers"] == [
            {
                "name": "gateway",
                "base_url": "https://gw.example/v1",
                "api_key": "!!! my gateway key 🐟",
                "model": "llama4-maverick",
            }
        ]

    def test_re_add_same_name_replaces(self, tmp_path, monkeypatch):
        import suijin.modules.platform.lib.config_loader as cl

        monkeypatch.setattr(cl, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(cl, "ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        first = iter(["http://old:8000/v1", "k1", "", "gw"])
        monkeypatch.setattr("builtins.input", lambda *a: next(first))
        cl.add_custom_provider()
        second = iter(["http://new:9000/v1", "k2", "m2", "gw"])
        monkeypatch.setattr("builtins.input", lambda *a: next(second))
        cl.add_custom_provider()
        cfg = json.loads((tmp_path / "config.json").read_text())
        entries = [e for e in cfg["custom_providers"] if e["name"] == "gw"]
        assert len(entries) == 1 and entries[0]["base_url"] == "http://new:9000/v1"

    def test_empty_base_url_aborts(self, tmp_path, monkeypatch):
        import suijin.modules.platform.lib.config_loader as cl

        monkeypatch.setattr(cl, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(cl, "ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda *a: "")
        assert cl.add_custom_provider() is False

    def test_headless_refuses_with_instructions(self, tmp_path, monkeypatch, capsys):
        import suijin.modules.platform.lib.config_loader as cl

        monkeypatch.setattr(cl, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(cl, "ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        assert cl.add_custom_provider() is False
        assert "custom_providers" in capsys.readouterr().out
