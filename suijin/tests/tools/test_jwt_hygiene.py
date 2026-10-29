"""JWT hygiene — token identity in the ledger, raw tokens in the store.

The 2026-10-28 trail echoed ONE JWT 23 times: short-lived tokens mint a
new signature per response and the raw-string dedupe saw each mint as a
fresh credential.
"""

import base64
import json
import time

from suijin.server.agent.lib.librarian import _jwt_fingerprint


def _mint(sub="user-a", role="user", exp_offset=3600, secret="sig-padding-0123456789"):
    hdr = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).decode().rstrip("=")
    pl = (
        base64.urlsafe_b64encode(json.dumps({"sub": sub, "role": role, "exp": int(time.time()) + exp_offset}).encode())
        .decode()
        .rstrip("=")
    )
    sig = base64.urlsafe_b64encode(secret.encode()).decode().rstrip("=")
    return f"{hdr}.{pl}.{sig}"


class TestFingerprint:
    def test_identity_stable_across_signature_refreshes(self):
        a = _mint(secret="sig1")
        b = _mint(secret="sig2")  # same identity, fresh signature
        ka, da = _jwt_fingerprint(a)
        kb, _ = _jwt_fingerprint(b)
        assert ka == kb
        assert da.startswith("JWT HS256 sub=user-a") and "EXPIRED" not in da

    def test_different_subjects_differ(self):
        assert _jwt_fingerprint(_mint(sub="admin"))[0] != _jwt_fingerprint(_mint(sub="user"))[0]

    def test_expired_flagged(self):
        key, disp = _jwt_fingerprint(_mint(exp_offset=-10))
        assert "EXPIRED" in disp

    def test_non_jwt_returns_none(self):
        assert _jwt_fingerprint("sk-live-abc123") is None
        assert _jwt_fingerprint("eyJh") is None  # too short to parse


class TestLedgerDedupe:
    def test_same_identity_different_signature_counts_once(self):
        from suijin.server.agent.lib.librarian import Librarian

        lb = Librarian.__new__(Librarian)  # bypass __init__ (no threads)
        lb._ledger = {"entries": [], "digests": []}
        lb._last_kind_counts = {}
        lb.run_id = "t"
        lb._target = "10.0.0.9"
        t1 = f"Status: 200\nAuthorization: Bearer {_mint(secret='sig-one-0123456789abcdef')}"
        t2 = f"Status: 200\nAuthorization: Bearer {_mint(secret='sig-two-0123456789abcdef')}"
        lb._extract("http_request", {"url": "http://t/x"}, t1, 1)
        lb._extract("http_request", {"url": "http://t/x"}, t2, 2)
        creds = [e for e in lb._ledger["entries"] if e["kind"] == "credential"]
        assert len(creds) == 1, "same token identity must count once"
        assert creds[0]["value"].startswith("JWT HS256")  # fingerprint, not raw
        assert "eyJ" not in creds[0]["value"]  # raw token never enters the ledger


class TestAbsentCataloguer:
    def test_404s_enter_the_ledger(self):
        from suijin.server.agent.lib.librarian import Librarian

        lb = Librarian.__new__(Librarian)
        lb._ledger = {"entries": [], "digests": []}
        lb._last_kind_counts = {}
        lb.run_id = "t"
        lb._target = "10.0.0.9"
        lb._extract("http_request", {"url": "http://10.0.0.9/v1/admin"}, "Status: 404\nBody:\nnot found", 8)
        absent = [e for e in lb._ledger["entries"] if e["kind"] == "absent endpoint"]
        assert len(absent) == 1
        assert absent[0]["value"] == "/v1/admin (404 @ iter 8)"

    def test_same_404_counts_once(self):
        from suijin.server.agent.lib.librarian import Librarian

        lb = Librarian.__new__(Librarian)
        lb._ledger = {"entries": [], "digests": []}
        lb._last_kind_counts = {}
        lb.run_id = "t"
        lb._target = "10.0.0.9"
        for _ in range(3):
            lb._extract("http_request", {"url": "http://10.0.0.9/v1/admin"}, "Status: 404", 8)
        assert len([e for e in lb._ledger["entries"] if e["kind"] == "absent endpoint"]) == 1

    def test_200s_do_not(self):
        from suijin.server.agent.lib.librarian import Librarian

        lb = Librarian.__new__(Librarian)
        lb._ledger = {"entries": [], "digests": []}
        lb._last_kind_counts = {}
        lb.run_id = "t"
        lb._target = "10.0.0.9"
        lb._extract("http_request", {"url": "http://10.0.0.9/api"}, "Status: 200\nBody:\nok", 9)
        assert [e for e in lb._ledger["entries"] if e["kind"] == "absent endpoint"] == []
