"""The half-dead-stream watchdog test (2026-09-12): iter_lines blocked in
SSL_read forever, requests' (10,120) timeout never fired. The watchdog
thread closes the response on inactivity → status 0 → the caller's
non-stream fallback recovers."""

import time


class FakeResp:
    def __init__(self, delay, lines):
        self._delay, self._lines, self.status_code = delay, lines, 200
        self.closed = False

    def close(self):
        self.closed = True
        if getattr(self, "_end", None):
            self._end.set()

    def iter_lines(self, decode_unicode=True):
        for line in self._lines:
            time.sleep(self._delay)
            yield line
        import threading as _th

        end = _th.Event()
        self._end = end
        while not end.is_set() and not self.closed:  # the half-dead socket
            time.sleep(0.5)
        if self.closed:
            raise OSError("connection closed by watchdog")  # what a real close does
        yield ""


def test_idle_stream_killed(monkeypatch):
    from suijin.modules.providers import lib as prov

    prov._STREAM_IDLE_S = 3.0  # test scale
    prov._STREAM_TOTAL_S = 60.0
    fake = FakeResp(0.2, ['data: {"choices":[{"delta":{"content":"hi"}}]}', "data: [DONE]"])
    # note: [DONE] breaks the loop before the hang — drop it to reach the hang
    fake._lines = ["data: " + __import__("json").dumps({"choices": [{"delta": {"content": "hi"}}]})]
    monkeypatch.setattr(prov._HTTP, "post", lambda *a, **k: _Ctx(fake))
    t0 = time.monotonic()
    status, content, _r, _u, body = prov._stream_chat("http://x", {}, {"model": "m", "messages": []})
    wall = time.monotonic() - t0
    assert status == 0, f"expected watchdog kill (status 0), got {status}: {body}"
    assert "idle" in body or "closed" in body or "error" in body.lower()
    assert wall < 30, f"watchdog too slow: {wall:.1f}s"
    assert content == "hi"  # whatever landed is preserved


class _Ctx:
    def __init__(self, r):
        self.r = r

    def __enter__(self):
        return self.r

    def __exit__(self, *a):
        return False


def test_healthy_stream_unaffected(monkeypatch):
    """A stream that reaches [DONE] never trips the watchdog."""
    from suijin.modules.providers import lib as prov

    prov._STREAM_IDLE_S = 3.0
    prov._STREAM_TOTAL_S = 60.0
    import json as _j

    fake = FakeResp(
        0.05,
        [
            "data: " + _j.dumps({"choices": [{"delta": {"content": "ok"}}]}),
            "data: [DONE]",
        ],
    )

    class _Ctx2(_Ctx):
        pass

    monkeypatch.setattr(prov._HTTP, "post", lambda *a, **k: _Ctx(fake))
    status, content, _r, _u, body = prov._stream_chat("http://x", {}, {"model": "m", "messages": []})
    assert status == 200 and content == "ok" and body == ""
