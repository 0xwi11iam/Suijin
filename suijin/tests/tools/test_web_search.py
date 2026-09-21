"""web_search — DuckDuckGo Lite HTML parsing.

The live markup drifts (attribute order and quote style both changed at
least once, silently zeroing results). The parser must survive both
shapes; the fixture HTML mirrors the 2026-09 live form (href first,
single-quoted classes) plus the legacy form.
"""

import pytest

from suijin.modules.tools.lib.web_search import web_search


def _legacy_html() -> str:
    return """
    <a rel="nofollow" class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fos.html">os — Python docs</a>
    <td class="result-snippet">Miscellaneous <b>os</b> module interfaces</td>
    <a rel="nofollow" class="result-link" href="https://example.com/second">Second Legacy</a>
    <td class="result-snippet">second snippet</td>
    """


def _current_html() -> str:
    # 2026-09 live shape: href FIRST, single-quoted class, direct URLs
    return """
    <a rel="nofollow" href="https://docs.python.org/3/library/os.html" class='result-link'>os — Python 3.14 docs</a>
    <td class='result-snippet'>
      Source code: Lib/<b>os</b>.py portable OS functionality.
    </td>
    <a rel="nofollow" href="https://peps.python.org/pep-0020/" class='result-link'>PEP 20</a>
    <td class='result-snippet'>The Zen of Python</td>
    """


@pytest.fixture()
def _offline(monkeypatch):
    import suijin.modules.tools.lib.web_search as ws

    def fake_post(url, headers=None, data=None, timeout=None):
        class R:
            status_code = 200
            text = _current_html() + _legacy_html()

        return R()

    monkeypatch.setattr(ws.requests, "post", fake_post)


def test_parses_current_single_quote_href_first_shape(_offline):
    out = web_search("anything", 5)
    assert "os — Python 3.14 docs" in out
    assert "https://docs.python.org/3/library/os.html" in out
    assert "portable OS functionality" in out  # snippet, tags stripped


def test_parses_legacy_double_quote_class_first_shape(_offline):
    out = web_search("anything", 5)
    assert "Second Legacy" in out
    # legacy uddg redirect unwraps to the real URL
    assert "https://docs.python.org/3/library/os.html" in out


def test_no_results_message(monkeypatch):
    import suijin.modules.tools.lib.web_search as ws

    def empty_post(url, headers=None, data=None, timeout=None):
        class R:
            status_code = 200
            text = "<html><body>nothing here</body></html>"

        return R()

    monkeypatch.setattr(ws.requests, "post", empty_post)
    out = web_search("zzz", 5)
    assert "No results found" in out


def test_empty_query():
    assert web_search("").startswith("Error")
