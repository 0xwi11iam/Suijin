"""web_fetch — readable text from pages (2026-10-31)."""

import suijin.server.tools.lib.web_search as _ws
from suijin.server.tools.lib.web_search import web_fetch

PAGE = """<html><head><style>body{color:red}</style><script>alert(1)</script></head>
<body><h1>Advisory</h1><p>CVE details <a href="/cve/2024-1">here</a></p>
<br/><div>Impact: high</div></body></html>"""


class TestWebFetch:
    def test_html_becomes_readable_text(self, monkeypatch):
        class R:
            status_code = 200
            text = PAGE

        monkeypatch.setattr(_ws.requests, "get", lambda *a, **k: R())
        out = web_fetch("https://t.example/advisory")
        assert "Advisory" in out and "Impact: high" in out
        assert "<script>" not in out and "color:red" not in out
        assert "[2024-1]" not in out  # link text, not raw path noise

    def test_links_kept_as_markdown(self, monkeypatch):
        class R:
            status_code = 200
            text = PAGE

        monkeypatch.setattr(_ws.requests, "get", lambda *a, **k: R())
        out = web_fetch("https://t.example/a")
        assert "](/cve/2024-1)" in out  # [here](/cve/2024-1)

    def test_truncation_pointer(self, monkeypatch):
        class R:
            status_code = 200
            text = "<p>" + "word " * 5000 + "</p>"

        monkeypatch.setattr(_ws.requests, "get", lambda *a, **k: R())
        out = web_fetch("https://t.example/big", max_chars=1000)
        assert "truncated at 1000" in out

    def test_error_shapes(self):
        assert web_fetch("").startswith("Error")
        assert web_fetch("ftp://x").startswith("Error")


def test_dispatch_routes_web_fetch():
    from suijin.server.tools.lib import dispatch

    out = dispatch.route_tool("web_fetch", {"url": ""}, config={})
    assert str(out).startswith("Error")  # clean error, no raise
