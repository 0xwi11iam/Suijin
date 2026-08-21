"""Bugscope — adapter tests pinned to the bbscope REFERENCE shapes.

Every mock below mirrors what the platform APIs actually return (cross-
checked against references/bbscope Go source), so a passing test means
the adapter parses reality, not a guess. Live verification happens
with real operator tokens later; `live-check` notes mark each platform.
"""

import json
from unittest import mock

from suijin.modules.bugscope import main as bs


class _Resp:
    def __init__(self, payload, status=200, text=None):
        self._p = payload
        self.status_code = status
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}", response=self)


# ── HackerOne (live-check: exact JSON:API shapes, verified vs reference) ──


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

    def test_basic_auth_form(self):
        captured = {}

        def grab(url, headers=None, **kw):
            captured.update(headers or {})
            return _Resp({"data": [], "links": {}})

        with mock.patch.object(bs.requests, "get", side_effect=grab):
            bs._pull_h1("u:t", None)
        assert captured.get("Authorization", "").startswith("Basic ")


# ── Bugcrowd (live-check: engagements.json + brief version document) ─────


class TestBugcrowd:
    def _list_page(self, briefs):
        return {
            "engagements": [{"briefUrl": b, "accessStatus": "open"} for b in briefs],
            "paginationMeta": {"totalCount": len(briefs)},
        }

    def test_engagement_list_and_scope_targets(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        list_page = self._list_page(["/engagements/acme"])
        program_html = '<script>const cfg = {"engagementBriefApi":{"getBriefVersionDocument":"/engagements/acme/briefversion/123.json"}};</script>'
        brief_doc = {
            "data": {
                "scope": [
                    {
                        "inScope": True,
                        "targets": [
                            {"name": "acme.com", "uri": "*.acme.com", "category": "website", "description": "main"}
                        ],
                    },
                    {"inScope": False, "targets": [{"uri": "blog.acme.com", "category": "website"}]},
                ]
            }
        }
        with mock.patch.object(
            bs.requests, "get", side_effect=[_Resp(list_page), _Resp({}, text=program_html), _Resp(brief_doc)]
        ):
            bs.scope_pull("bugcrowd", "sess-tok")
        rows = json.loads((tmp_path / "outputs" / "bugscope" / "bugcrowd.json").read_text())
        by_asset = {r["asset"]: r for r in rows}
        assert by_asset["*.acme.com"]["eligible"] is True
        assert by_asset["*.acme.com"]["type"] == "website"
        assert by_asset["blog.acme.com"]["eligible"] is False  # inScope=False respected
        assert rows[0]["source"] == "https://bugcrowd.com/engagements/acme"

    def test_program_without_brief_api_returns_empty(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        with mock.patch.object(
            bs.requests,
            "get",
            side_effect=[_Resp(self._list_page(["/engagements/x"])), _Resp({}, text="<html>no api</html>")],
        ):
            bs.scope_pull("bugcrowd", "t")
        rows = json.loads((tmp_path / "outputs" / "bugscope" / "bugcrowd.json").read_text())
        assert rows == []


# ── YesWeHack (live-check: list slugs, detail scopes[].scope) ─────────────


class TestYWH:
    def test_list_then_detail_with_out_of_scope(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        listing = {"items": [{"slug": "acme"}], "pagination": {"next": False}}
        detail = {
            "scopes": [
                {"scope": "*.acme.com", "scope_type": "web-application", "enabled": True},
                {"scope": "legacy.acme.com", "scope_type": "web-application", "enabled": True},
            ],
            "out_of_scope": [{"scope": "legacy.acme.com"}],
        }
        with mock.patch.object(bs.requests, "get", side_effect=[_Resp(listing), _Resp(detail)]):
            bs.scope_pull("ywh", "tok")
        rows = json.loads((tmp_path / "outputs" / "bugscope" / "ywh.json").read_text())
        by_asset = {r["asset"]: r for r in rows}
        assert by_asset["*.acme.com"]["eligible"] is True
        assert by_asset["legacy.acme.com"]["eligible"] is False  # out_of_scope wins
        assert by_asset["*.acme.com"]["type"] == "web-application"


# ── Intigriti (live-check: external/researcher/v1, offset accumulate) ────


class TestIntigriti:
    def test_offset_accumulation_until_total(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        page1 = {
            "records": [
                {
                    "handle": "acme",
                    "domains": [
                        {
                            "type": "url",
                            "endpoint": "fallback",
                            "content": [{"endpoint": "https://acme.com"}, {"endpoint": "https://api.acme.com"}],
                        }
                    ],
                }
            ],
            "total": 1,
        }
        empty = {"records": [], "total": 1}
        calls = []

        def grab(url, headers=None, **kw):
            calls.append(url)
            return _Resp(page1 if len(calls) == 1 else empty)

        with mock.patch.object(bs.requests, "get", side_effect=grab):
            bs.scope_pull("intigriti", "tok")
        assert any("offset=0" in u for u in calls)
        rows = json.loads((tmp_path / "outputs" / "bugscope" / "intigriti.json").read_text())
        assert {r["asset"] for r in rows} == {"https://acme.com", "https://api.acme.com"}


# ── Immunefi (live-check: RSC scrape, bounties + assets arrays) ───────────


class TestImmunefi:
    def test_rsc_list_and_assets(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        listing_html = 'x{"bounties":[{"slug":"acme","inviteOnly":false},{"slug":"secret","inviteOnly":true}]}y'
        program_html = (
            'data = {"assets":[{"url":"https://acme.io","type":"asset","category":"web","description":"primary"}]};'
        )
        with mock.patch.object(
            bs.requests, "get", side_effect=[_Resp({}, text=listing_html), _Resp({}, text=program_html)]
        ):
            bs.scope_pull("immunefi", "tok")
        rows = json.loads((tmp_path / "outputs" / "bugscope" / "immunefi.json").read_text())
        assert len(rows) == 1  # invite-only skipped
        assert rows[0]["asset"] == "https://acme.io"
        assert rows[0]["type"] == "web"
        assert rows[0]["source"].endswith("/acme/")


# ── cross-cutting ─────────────────────────────────────────────────────────


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

    def test_verify_true_on_every_request(self):
        seen = {}

        def grab(url, headers=None, verify=None, **kw):
            seen["verify"] = verify
            return _Resp({"data": [], "links": {}})

        with mock.patch.object(bs.requests, "get", side_effect=grab):
            bs._pull_h1("u:t", None)
        assert seen["verify"] is True


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
