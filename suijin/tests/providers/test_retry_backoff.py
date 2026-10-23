"""Retry backoff for provider transport failures.

Field 2026-10-23: a ~30s DNS blip ended a healthy engagement — the whole
failover chain (2s+4s per provider) gave up before DNS returned. Transport
class failures now back off 5s·2^n (capped 30s); HTTP-class errors keep the
fast 2s·2^n curve (failover is cheaper than waiting out a 429).
"""

from suijin.modules.providers.lib import _retry_sleep, _transient_transport


class TestTransientTransport:
    def test_dns_and_connection_are_transient(self):
        assert _transient_transport("DNS failure — the API hostname did not resolve [ConnectionError]")
        assert _transient_transport("connection failed — reset/refused mid-transport")
        assert _transient_transport("read timeout — no bytes within the read window")
        assert _transient_transport("TLS handshake failed")

    def test_http_errors_are_not(self):
        assert not _transient_transport("rate-limited (429)")
        assert not _transient_transport("model 'glm-5.3' not found")
        assert not _transient_transport('{"error": "bad request"}')

    def test_transport_gets_the_steep_curve(self):
        assert _retry_sleep(0, "DNS failure") == 5
        assert _retry_sleep(1, "DNS failure") == 10
        assert _retry_sleep(3, "connection failed") == 30  # capped

    def test_http_keeps_the_fast_curve(self):
        assert _retry_sleep(0, "rate-limited (429)") == 2
        assert _retry_sleep(1, "rate-limited (429)") == 4
