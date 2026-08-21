"""Bugscope — scope scraper tests (network mocked; SSL refusal live-checked)."""

import json
from unittest import mock

from suijin.modules.bugscope import main as bs


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}", response=self)


class TestH1:
    def test_programs_and_scope_pages(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        programs_page = {"data": [{"attributes": {"handle": "github"}}], "links": {"next": None}}
        scope_page = {
            "data": [
                {"attributes": {"asset_identifier": "*.github.com", "asset_type": "URL", "eligible_for_bounty": True}},
                {
                    "attributes": {
                        "asset_identifier": "api.github.com",
                        "asset_type": "URL",
                        "eligible_for_bounty": False,
                    }
                },
            ],
            "links": {"next": None},
        }
        with mock.patch.object(bs.requests, "get", side_effect=[_Resp(programs_page), _Resp(scope_page)]):
            out = bs.scope_pull("h1", "user:tok")
        assert "pulled 2 scope entries across 1 program" in out
        rows = json.loads((tmp_path / "outputs" / "bugscope" / "h1.json").read_text())
        assert rows[0]["asset"] == "*.github.com" and rows[0]["eligible"] is True

    def test_bearer_auth_header_not_basic(self):
        captured = {}

        def grab(url, headers=None, **kw):
            captured.update(headers or {})
            return _Resp({"data": [], "links": {}})

        with mock.patch.object(bs.requests, "get", side_effect=grab):
            bs._pull_h1("u:t", None)
        assert captured.get("Authorization", "").startswith("Basic ")

    def test_program_filter(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        progs = {"data": [{"attributes": {"handle": "a"}}, {"attributes": {"handle": "b"}}], "links": {}}
        empty = {"data": [], "links": {}}
        with mock.patch.object(bs.requests, "get", side_effect=[_Resp(progs), _Resp(empty)]):
            bs.scope_pull("h1", "u:t", programs="a")
        rows = json.loads((tmp_path / "outputs" / "bugscope" / "h1.json").read_text())
        assert all(r["program"] == "a" for r in rows)


class TestSearch:
    def test_offline_search_in_scope_only(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        d = tmp_path / "outputs" / "bugscope"
        d.mkdir(parents=True)
        (d / "h1.json").write_text(
            json.dumps(
                [
                    {"program": "x", "asset": "shop.example.com", "type": "URL", "eligible": True},
                    {"program": "x", "asset": "internal.example.com", "type": "URL", "eligible": False},
                ]
            )
        )
        out = bs.scope_search("example.com")
        assert "shop.example.com" in out and "internal.example.com" not in out
        assert "scope_pull" in bs.scope_search("nothing-matches-this")


class TestErrors:
    def test_bad_platform(self):
        assert "platform must be one of" in bs.scope_pull("ddos", "t")

    def test_missing_token(self):
        assert "token required" in bs.scope_pull("h1", "")

    def test_ssl_refusal_is_explicit(self):
        import requests

        with mock.patch.object(bs.requests, "get", side_effect=requests.exceptions.SSLError("cert verify failed")):
            out = bs.scope_pull("h1", "u:t")
        assert "SSL verification failed" in out and "refusing insecure fallback" in out

    def test_401_points_at_token(self):
        import requests

        resp = _Resp({}, status=401)
        with mock.patch.object(bs.requests, "get", side_effect=requests.HTTPError("401", response=resp)):
            out = bs.scope_pull("ywh", "bad")
        assert "HTTP 401" in out and "check the token" in out


class TestVerifyAlways:
    def test_verify_true_on_every_request(self):
        seen = {}

        def grab(url, headers=None, verify=None, **kw):
            seen["verify"] = verify
            return _Resp({"data": [], "links": {}})

        with mock.patch.object(bs.requests, "get", side_effect=grab):
            bs._pull_h1("u:t", None)
        assert seen["verify"] is True  # bbscope ships InsecureSkipVerify; we never do
