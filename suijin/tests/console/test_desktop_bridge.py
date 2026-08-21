"""Desktop bridge — agent <-> gateway HITL is REAL, not parallel stores.

approvals: the gateway reads/writes the very store the agent polls
(ops approvals module) — approving on the desktop unblocks the running
agent's next attempt at that tool.
ask_operator: detached engagements (stdin dead) persist the question
where the gateway serves it; fetch_answer polls for the operator's
reply.
"""

import json
import time

import pytest


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    from suijin.modules.platform.lib import workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    from suijin.modules.console.lib import gateway as gw
    from suijin.modules.ops.lib import approvals as ap

    ap.APPROVALS_PATH = tmp_path / "approvals.json"
    return gw, ap, tmp_path


class TestApprovalsBridge:
    def test_gateway_decide_unblocks_agent(self, stores):
        gw, ap, _ = stores
        from fastapi.testclient import TestClient

        # the AGENT files a pending request (exactly as modes.py does)
        pid = ap.record_pending("nmap", {"cmd": "nmap -sS 10.10.10.5"})
        assert ap.decision_for("nmap") == "none"  # no session verdict yet

        # the OPERATOR approves via the gateway
        c = TestClient(gw.create_app(token="t"))
        r = c.post(
            f"/api/approvals/{pid}", headers={"Authorization": "Bearer t"}, json={"action": "approve", "note": "go"}
        )
        assert r.status_code == 200

        # the AGENT's next check sees it
        assert ap.decision_for("nmap") == "approved"
        item = next(i for i in ap.list_approvals() if i["id"] == pid)
        assert item["note"] == "go"

    def test_deny_blocks_agent(self, stores):
        gw, ap, _ = stores
        from fastapi.testclient import TestClient

        pid = ap.record_pending("rm", {"cmd": "rm -rf /"})
        c = TestClient(gw.create_app(token="t"))
        c.post(f"/api/approvals/{pid}", headers={"Authorization": "Bearer t"}, json={"action": "deny"})
        assert ap.decision_for("rm") == "denied"

    def test_gateway_lists_agent_filed_items(self, stores):
        gw, ap, _ = stores
        from fastapi.testclient import TestClient

        ap.record_pending("sqlmap", {"cmd": "sqlmap -u http://t"})
        c = TestClient(gw.create_app(token="t"))
        items = c.get("/api/approvals", headers={"Authorization": "Bearer t"}).json()
        assert any(i["tool"] == "sqlmap" for i in items)


class TestAskOperatorBridge:
    def test_push_then_answer_roundtrip(self, stores):
        gw, _ap, tmp = stores
        qid = gw.push_question("Should I pivot to 10.10.10.0/24?")
        # the file the gateway serves
        qs = json.loads((tmp / "outputs" / "questions.jsonl").read_text().splitlines()[0])
        assert qs["id"] == qid and qs["answered"] is False

        # the OPERATOR answers via the gateway API
        from fastapi.testclient import TestClient

        c = TestClient(gw.create_app(token="t"))
        r = c.post(
            f"/api/questions/{qid}", headers={"Authorization": "Bearer t"}, json={"answer": "No — stay in scope"}
        )
        assert r.json()["answered"] is True

        # the AGENT's poll returns it immediately
        assert gw.fetch_answer(qid, timeout_s=2, poll_s=0.05) == "No — stay in scope"

    def test_fetch_timeout_returns_none(self, stores):
        gw, _ap, _ = stores
        qid = gw.push_question("anyone there?")
        t0 = time.monotonic()
        assert gw.fetch_answer(qid, timeout_s=0.4, poll_s=0.1) is None
        assert time.monotonic() - t0 < 2

    def test_answered_via_file_directly(self, stores):
        """The gateway's questions route writes the same file fetch_answer polls."""
        gw, _ap, tmp = stores
        qid = gw.push_question("q?")
        # simulate the desktop having answered (file-level, same as POST route)
        items = gw._questions_read()
        for q in items:
            if q["id"] == qid:
                q["answered"] = True
                q["answer"] = "file-level yes"
        gw._questions_write(items)
        assert gw.fetch_answer(qid, timeout_s=1, poll_s=0.05) == "file-level yes"
