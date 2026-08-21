"""Wave 2 batch 2: finding verifier (A1), peer review (A2), compaction (A7)."""

import json

from suijin.modules.agent.lib.compact import compact, history_chars, needs_compaction
from suijin.modules.agent.lib.verify import peer_review, render_review, verify_findings


def _route(tool, args, config):
    if tool == "scan_secrets":
        return "1 finding(s):\nAWS key @line 3: AKIAIOSFODNN7EXAMPLE"
    if tool == "cors_check":
        return "ACAO: https://evil.example | ACAC: true\nREFLECTS arbitrary Origin + CREDENTIALS"
    if tool == "open_redirect_check":
        return "OPEN REDIRECT: next=... -> 30x Location=https://example.org/safe-canary"
    if tool == "extract_artifacts":
        return "urls (1):\n  https://t/x\nsecret (0)"
    return "some unrelated output"


class TestVerifier:
    def test_secret_finding_independently_verified(self):
        f = {
            "type": "secret",
            "target": "https://t/config.js",
            "evidence": "AKIAIOSFODNN7EXAMPLE",
            "confidence": "probable",
        }
        out = verify_findings([f], route_fn=_route)[0]
        assert out["verification"]["verdict"] == "verified"

    def test_unknown_type_unverifiable(self):
        f = {"type": "exotic", "target": "x", "evidence": "y", "confidence": "probable"}
        out = verify_findings([f], route_fn=_route)[0]
        assert out["verification"]["verdict"] == "unverifiable"

    def test_verified_confidence_skipped_when_asked(self):
        f = {"type": "secret", "target": "t", "evidence": "e", "confidence": "verified"}
        out = verify_findings([f], route_fn=_route)[0]
        assert "verification" not in out  # already verified, untouched

    def test_contradicted_dismissed(self):
        def contra(tool, args, config):
            return "nothing relevant here at all"

        f = {"type": "secret", "target": "https://t/x.js", "evidence": "AKIA...", "confidence": "probable"}
        out = verify_findings([f], route_fn=contra)[0]
        # marker absent -> not verified; expect_absent families are error-based... downgrade or dismissed
        assert out["verification"]["verdict"] in ("downgraded", "dismissed")


class TestPeerReview:
    def test_no_llm_marks_keep(self):
        class Boom:
            async def __call__(self, *a, **k):
                raise RuntimeError("no key")

        out = peer_review([{"type": "sqli", "evidence": "e"}], generate_fn=Boom())
        assert out["source"] == "no-llm"
        assert out["reviewed"][0]["peer_review"]["verdict"] == "keep"

    def test_llm_verdicts_applied(self):
        async def gen(messages, config=None, **kw):
            q = messages[0]["content"]
            if "hostile reviewer" in q:
                return json.dumps({"attacks": [{"id": 1, "attack": "no proof", "severity": "fatal"}]})
            return json.dumps({"verdicts": [{"id": 1, "verdict": "dismiss", "reason": "attack stands"}]})

        out = peer_review([{"type": "sqli", "evidence": "e"}], generate_fn=gen)
        assert out["source"] == "llm"
        assert out["reviewed"][0]["peer_review"]["verdict"] == "dismiss"

    def test_render(self):
        rows = render_review(
            [{"type": "xss", "confidence": "probable", "evidence": "<script>", "verification": {"verdict": "verified"}}]
        )
        assert "verify=verified" in rows


class TestCompaction:
    def _messages(self, n, size=4000):
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(n):
            tag = "ok" if i % 3 else "Error: boom"
            msgs.append({"role": "user", "content": f"RESULT (tool_{i % 5}, 100ms, iteration 1): {tag} " + "x" * size})
        return msgs

    def test_under_budget_noop(self):
        msgs = self._messages(5)
        assert compact(msgs) is msgs
        assert not needs_compaction(msgs)

    def test_over_budget_compacts_and_keeps_tail(self):
        msgs = self._messages(50)
        assert needs_compaction(msgs)
        out = compact(msgs)
        assert out is not msgs
        assert history_chars(out) < history_chars(msgs) // 2
        assert out[0]["role"] == "system"  # system preserved
        assert out[-1] is msgs[-1]  # recent tail verbatim
        assert "CONTEXT COMPACTED" in out[1]["content"]
        assert "do not repeat blindly" in out[1]["content"]

    def test_never_mutates_input(self):
        msgs = self._messages(50)
        before = [dict(m) for m in msgs]
        compact(msgs)
        assert msgs == before
