"""Three field regressions, all from one live engagement transcript.

1. STREAM DECODE — provider SSE lines were decoded per-chunk with requests'
   fallback encoding (latin-1), so any multi-byte character split across a
   chunk boundary arrived as mojibake: NVIDIA's → NVIDIAâs. The agent's own
   oracle flagged it mid-run ("Encoding mismatch — character encoding caused
   truncation or garbling"). Lines are now decoded as UTF-8 by us, on raw
   bytes — a complete line can never split a codepoint.

2. COOKIE CONFLICT — after a redirect, Akamai set ak_bmsc on TWO domains
   (nvidia.com and www.nvidia.com). dict(jar) indexes the jar by name,
   which raises CookieConflictError, and a response that had already
   SUCCEEDED was discarded as "HTTP Error: multiple cookies with name
   'ak_bmsc'" — twice — blocking robots.txt entirely.

3. SUPERPLIER MIS nag — the repeat detector counted tool names only, so
   two FAILED calls plus one retry read as "called 3 times in a row. STOP
   and try a DIFFERENT approach" — punishing the agent for working around
   our own bug.
"""

from __future__ import annotations

import json

import pytest

# ── 1. stream decode ────────────────────────────────────────────────────


class _Latin1ChunkResp:
    """Faithful to the OLD behavior: honoring decode_unicode=True the way
    requests does when an SSE response declares no charset — decode each
    chunk with the latin-1 fallback, splitting multi-byte characters."""

    encoding = None
    status_code = 200
    text = ""

    def __init__(self, byte_lines):
        self._byte_lines = byte_lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def iter_lines(self, decode_unicode=True):
        for raw in self._byte_lines:
            if decode_unicode:
                # the buggy path: per-chunk latin-1 decode, split codepoints
                yield raw.decode("latin-1")
            else:
                yield raw


def test_stream_decodes_utf8_lines_cleanly(monkeypatch):
    """Multi-byte characters in the stream arrive intact, not as â soup."""
    from suijin.modules.providers import lib as prov

    payload = "NVIDIA’s automation — restrictions 中文字符"
    sse = ("data: " + json.dumps({"choices": [{"delta": {"content": payload}, "finish_reason": None}]})).encode("utf-8")
    fake = _Latin1ChunkResp([sse, b"", b"data: [DONE]"])
    monkeypatch.setattr(prov._HTTP, "post", lambda *a, **k: fake)
    status, content, _r, _u, _body = prov._stream_chat("http://x", {}, {"model": "m", "messages": []})
    assert status == 200
    assert payload in content, f"mojibake returned: {content!r}"
    assert "â" not in content


def test_stream_survives_a_byte_line_split_mid_codepoint(monkeypatch):
    """A line that arrives as multiple raw byte fragments (transport
    chunking) still decodes per fragment without crashing; complete SSE
    lines are the unit, so no valid line ever splits a character."""
    from suijin.modules.providers import lib as prov

    part1 = 'data: {"choices":[{"delta":{"content":"don'.encode("utf-8")
    part2 = '’t stop"}}]}'.encode("utf-8")
    fake = _Latin1ChunkResp([part1, part2, b"data: [DONE]"])
    monkeypatch.setattr(prov._HTTP, "post", lambda *a, **k: fake)
    status, content, _r, _u, _body = prov._stream_chat("http://x", {}, {"model": "m", "messages": []})
    assert status == 200  # partial JSON fragments are skipped, nothing crashes


def test_stream_encoding_is_forced_before_iteration():
    """The old bug starts with resp.encoding being None; belt-and-braces:
    the source forces UTF-8 and iterates raw bytes."""
    import inspect

    from suijin.modules.providers import lib as prov

    src = inspect.getsource(prov._stream_chat)
    assert 'resp.encoding = "utf-8"' in src
    assert "iter_lines(decode_unicode=False)" in src


# ── 2. cookie conflict ──────────────────────────────────────────────────


class _FakeResp:
    status_code = 200
    headers = {"Content-Type": "text/plain"}
    text = "User-agent: *\nDisallow: /private\n"


def test_duplicate_cookie_names_do_not_discard_the_response(monkeypatch):
    """Akamai's ak_bmsc on two domains raised CookieConflictError out of
    dict(jar) and threw away a successful robots.txt fetch."""
    import requests

    from suijin.modules.tools.lib import http_tools

    jar = requests.cookies.RequestsCookieJar()
    jar.set("ak_bmsc", "AAA", domain=".nvidia.com", path="/")
    jar.set("ak_bmsc", "BBB", domain="www.nvidia.com", path="/")
    # the old code would already blow up here:
    with pytest.raises(requests.cookies.CookieConflictError):
        dict(jar)

    class _Sess:
        cookies = jar

        def request(self, **kw):
            return _FakeResp()

    monkeypatch.setattr(http_tools, "global_session", _Sess())
    monkeypatch.setattr("suijin.modules.tools.lib.session_aware.is_rate_limited", lambda url: False)
    monkeypatch.setattr("suijin.modules.tools.lib.session_aware.record_response", lambda *a, **k: None)

    out = http_tools.http_request("GET", "https://nvidia.com/robots.txt")
    assert out.startswith("Status: 200"), out[:200]
    assert "HTTP Error" not in out
    assert "ak_bmsc=AAA" in out and "ak_bmsc=BBB" in out  # both shown, harmlessly


# ── 3. supervisor repeat nag ────────────────────────────────────────────


def test_repeat_nag_ignores_errored_calls():
    """Two FAILED calls + one retry is the agent working around a bug, not
    a rut — no 'STOP and try a DIFFERENT approach' nag."""
    from suijin.modules.agent.lib.supervisor import _detect_repeating_tool

    trace = [
        {"tool_name": "http_request", "success": False, "error_class": "http_error"},
        {"tool_name": "http_request", "success": False, "error_class": "http_error"},
        {"tool_name": "http_request", "success": True},
    ]
    assert _detect_repeating_tool(trace) is None


def test_repeat_nag_still_fires_on_real_repetition():
    from suijin.modules.agent.lib.supervisor import _detect_repeating_tool

    trace = [
        {"tool_name": "http_request", "success": True},
        {"tool_name": "http_request", "success": True},
        {"tool_name": "http_request", "success": True},
    ]
    msg = _detect_repeating_tool(trace)
    assert msg and "3 times in a row" in msg


def test_repeat_nag_counts_only_the_recent_window():
    from suijin.modules.agent.lib.supervisor import _detect_repeating_tool

    trace = [
        {"tool_name": "nmap_scan", "success": True},
        {"tool_name": "http_request", "success": True},
        {"tool_name": "http_request", "success": False, "error_class": "cookie_conflict"},
        {"tool_name": "http_request", "success": True},
    ]
    assert _detect_repeating_tool(trace) is None  # only 2 successes in the window


# ── ask-answer delivery (the target loop) ───────────────────────────────


class TestAskAnswerNamesTheTarget:
    """The agent asked for a target; the operator answered with a hostname;
    the agent concluded 'the operator just repeated the order' and
    re-deliberated. The injected answer must be unambiguous about being
    FINAL and about any host in it being the approved target."""

    def test_target_regex_matches_hosts_and_ips(self):
        from suijin.modules.redteam.lib.redteamer import _ANSWER_TARGET_RE

        assert _ANSWER_TARGET_RE.search("test nvidia.com and stay low volume")
        assert _ANSWER_TARGET_RE.search("go for www.nvidia.com")
        assert _ANSWER_TARGET_RE.search("the box is 192.168.1.10")
        assert not _ANSWER_TARGET_RE.search("yes, it is authorized, proceed")
        assert not _ANSWER_TARGET_RE.search("continue as you see fit")

    def test_confirmation_is_prepended_not_buried(self):
        """The [OPERATOR-CONFIRMED] marker goes at the START of the
        objective — a suffix at the end of a 13k-character order is never
        seen, which is exactly what read as 'the order was repeated'."""

        from suijin.modules.redteam.lib import redteamer

        src = inspect_source(redteamer)
        # the built objective must start with the marker, not end with it
        assert '"[OPERATOR-CONFIRMED: {answer[:160]}]\\n{objective}"' in src
        # the injected message must say FINAL so the agent stops re-asking
        assert "OPERATOR ANSWER (FINAL" in src

    def test_designation_and_confirmation_both_persist(self):
        import inspect

        from suijin.modules.redteam.lib import redteamer

        src = inspect.getsource(redteamer)
        assert "_names_target or _looks_like_scope_confirmation(answer)" in src


def inspect_source(mod):
    import inspect

    return inspect.getsource(mod)
