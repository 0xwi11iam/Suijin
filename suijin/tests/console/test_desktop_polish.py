"""Desktop polish — fireteam state mirror, gateway endpoint, discovery.

The fireteam screen is only an asset if the DATA is real: the registry
mirror must reflect deploy/drain lifecycles cross-process, and the
gateway must serve it. Discovery must be written 0600 and cleaned up.
"""

import json

import pytest

from suijin.modules.agent.lib.nodes import subagent_node as sn


@pytest.fixture()
def fireteam_env(tmp_path, monkeypatch):
    from suijin.modules.platform.lib import workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    sn._reset_fireteams()
    yield tmp_path
    sn._reset_fireteams()


def _fake_gen_ok():
    import json as j

    script = [
        j.dumps({"action": "complete", "completion_reason": "found flag in /api", "thought": "t"}),
    ]

    async def gen(messages, config=None, **kw):
        await __import__("asyncio").sleep(0.05)
        return script.pop(0) if script else script[-1] if script else "{}"

    return gen


class TestStateMirror:
    def test_deploy_persists_running_team(self, fireteam_env):
        import asyncio

        async def go():
            return sn.deploy_fireteam(
                ["Probe http://t.example/api for the auth flag"],
                generate_fn=_fake_gen_ok(),
                route_tool_fn=lambda *a: "ok",
            )

        dep = asyncio.run(go())
        assert dep["team_id"]
        state = json.loads((fireteam_env / "outputs" / "fireteam" / "registry.json").read_text())
        team = state["teams"][0]
        assert team["team_id"] == dep["team_id"]
        assert team["running"] >= 1
        assert team["tasks"][0]["task"].startswith("Probe")
        assert team["tasks"][0]["state"] in ("running", "queued", "done")  # timing-dependent

    def test_drain_updates_state_and_forgets_team(self, fireteam_env):
        import asyncio

        async def go():
            dep = sn.deploy_fireteam(
                ["Check http://t.example/robots.txt for hidden admin paths"],
                generate_fn=_fake_gen_ok(),
                route_tool_fn=lambda *a: "200 ok",
            )
            await asyncio.sleep(0.3)  # specialist finishes
            msgs = sn.collect_finished_teams()
            return dep, msgs

        dep, msgs = asyncio.run(go())
        assert any("FIRETEAM RESULT" in m for m in msgs)
        state = json.loads((fireteam_env / "outputs" / "fireteam" / "registry.json").read_text())
        # fully drained -> the team is forgotten
        assert all(t["team_id"] != dep["team_id"] for t in state["teams"])

    def test_reset_clears_file(self, fireteam_env):
        import asyncio

        async def go():
            return sn.deploy_fireteam(
                ["Test SSTI on http://t.example/render with {{7*7}} payloads"],
                generate_fn=_fake_gen_ok(),
                route_tool_fn=lambda *a: "ok",
            )

        asyncio.run(go())
        assert (fireteam_env / "outputs" / "fireteam" / "registry.json").exists()
        sn._reset_fireteams()
        state = json.loads((fireteam_env / "outputs" / "fireteam" / "registry.json").read_text())
        assert state["teams"] == []


class TestGatewayFireteam:
    def test_endpoint_serves_the_mirror(self, fireteam_env):
        from fastapi.testclient import TestClient

        from suijin.modules.console.lib.gateway import create_app

        # write a mirror the way the agent does
        mirror = {
            "teams": [
                {
                    "team_id": "team-abc",
                    "started": "2026-08-22T00:00:00",
                    "running": 1,
                    "tasks": [{"task": "probe", "state": "running", "success": None, "steps": None, "findings": ""}],
                }
            ],
            "updated": "2026-08-22T00:00:01",
        }
        d = fireteam_env / "outputs" / "fireteam"
        d.mkdir(parents=True, exist_ok=True)
        (d / "registry.json").write_text(json.dumps(mirror))

        c = TestClient(create_app(token="t"))
        r = c.get("/api/fireteam", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        assert r.json()["teams"][0]["team_id"] == "team-abc"

    def test_endpoint_falls_back_to_empty_snapshot(self, fireteam_env):
        from fastapi.testclient import TestClient

        from suijin.modules.console.lib.gateway import create_app

        c = TestClient(create_app(token="t"))
        r = c.get("/api/fireteam", headers={"Authorization": "Bearer t"})
        assert r.status_code == 200 and r.json()["teams"] == []


class TestDiscoveryLifecycle:
    def test_write_and_clear(self, tmp_path, monkeypatch):
        from suijin.modules.console.lib import gateway as gw

        monkeypatch.setattr(gw, "_discovery_path", lambda: tmp_path / "gateway.json")
        gw._write_discovery("127.0.0.1", 7331, "secret")
        f = tmp_path / "gateway.json"
        assert f.exists()
        data = json.loads(f.read_text())
        assert data["url"] == "http://127.0.0.1:7331" and data["token"] == "secret"
        assert (f.stat().st_mode & 0o777) == 0o600  # token file is private
        gw._clear_discovery()
        assert not f.exists()

    def test_clear_missing_is_noop(self, tmp_path, monkeypatch):
        from suijin.modules.console.lib import gateway as gw

        monkeypatch.setattr(gw, "_discovery_path", lambda: tmp_path / "nope.json")
        gw._clear_discovery()  # must not raise
