"""Wave 6: F41-F43 marketplace, G47-G50 learning finishers + exfil."""

import json
import zipfile
from unittest import mock

from suijin.modules.blueteam.lib.blue import exfil


class TestMarketplace:
    def _index(self, tmp_path, sha=None):
        idx = {
            "packs": {
                "demo": {
                    "version": "1.1",
                    "description": "demo pack",
                    "url": "file://" + str(tmp_path / "p.zip"),
                    **({"sha256": sha} if sha else {}),
                }
            }
        }
        ip = tmp_path / "index.json"
        ip.write_text(json.dumps(idx))
        return "file://" + str(ip)

    def _pack_zip(self, tmp_path):
        z = tmp_path / "p.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr(
                "demo/plugin.json",
                json.dumps(
                    {
                        "id": "demo",
                        "version": "1.1",
                        "tier": "recommended",
                        "requires": ["platform"],
                        "provides": [],
                        "entry": "demo:M",
                        "description": "d",
                    }
                ),
            )
            zf.writestr(
                "demo/main.py",
                "from suijin.kernel.contracts import Module, Tier\n\n\nclass M(Module):\n    id='demo'\n    tier=Tier.RECOMMENDED\n    def register(self, ctx): pass\n    def start(self, ctx): pass\n    def stop(self, ctx): pass\n",
            )
        import hashlib

        return hashlib.sha256(z.read_bytes()).hexdigest()

    def test_search_and_install_pinned(self, tmp_path):
        from suijin.modules import marketplace as mp

        sha = self._pack_zip(tmp_path)
        idx = self._index(tmp_path, sha)
        assert mp.search("demo", idx)[0]["id"] == "demo"
        into = tmp_path / "dest"
        out = mp.install_pack("demo", idx, into=into / "demo")
        assert out.startswith("installed demo v1.1") and (into / "demo" / "plugin.json").exists()

    def test_hash_mismatch_refused(self, tmp_path):
        from suijin.modules import marketplace as mp

        self._pack_zip(tmp_path)
        idx = self._index(tmp_path, sha="0" * 64)
        out = mp.install_pack("demo", idx, into=tmp_path / "x" / "demo")
        assert "HASH MISMATCH" in out and not (tmp_path / "x").exists()

    def test_unknown_pack(self, tmp_path):
        from suijin.modules import marketplace as mp

        idx = self._index(tmp_path)
        assert "no pack" in mp.install_pack("nope", idx)

    def test_update_rolls_back_on_failure(self, tmp_path):
        from suijin.modules import marketplace as mp

        sha = self._pack_zip(tmp_path)
        idx = self._index(tmp_path, sha)
        dest = tmp_path / "mods" / "demo"
        mp.install_pack("demo", idx, into=dest)
        # break the index url -> update fails -> rollback keeps v1.1
        self._index(tmp_path, sha)
        data = json.loads((tmp_path / "index.json").read_text())
        data["packs"]["demo"]["url"] = "file:///nonexistent.zip"
        (tmp_path / "index.json").write_text(json.dumps(data))
        (
            mp.update_pack.__wrapped__(dest.parent, "demo", "file://" + str(tmp_path / "index.json"))
            if hasattr(mp.update_pack, "__wrapped__")
            else None
        )
        # update_pack targets the USER dir; simulate with a monkeypatched dir
        with mock.patch.object(mp, "_user_modules_dir", return_value=dest.parent):
            res = mp.update_pack("demo", "file://" + str(tmp_path / "index.json"))
        assert "rolled back" in res and (dest / "plugin.json").exists()


class TestLearningFinishers:
    def test_promote_dry_run_and_write(self, tmp_path, monkeypatch):
        from suijin.modules.agent.lib import critique
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        rdir = tmp_path / "outputs" / "reports"
        rdir.mkdir(parents=True)
        (rdir / "critique_e1.md").write_text("# c\n\n## Tactics to remember\n- always probe /api/v2 first\n")
        dry = critique.promote_learnings(dry_run=True)
        assert "/api/v2" in dry and "dry run" in dry

    def test_decay_report(self, tmp_path, monkeypatch):
        import suijin.modules.skills.entry as entry
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        drop = tmp_path / "skillsdrop"
        drop.mkdir()
        (drop / "used-skill.md").write_text("## cors_check\nuse cors_check first")
        (drop / "ancient-skill.md").write_text("## telnet_bounce\nrare telnet_bounce trick")
        trails = tmp_path / "outputs" / "audit_trails"
        trails.mkdir(parents=True)
        (trails / "e.json").write_text(json.dumps({"engagement": "e", "iterations": [{"tool": "cors_check"}]}))
        with mock.patch.object(entry, "_drop_roots", lambda: [drop]):
            out = entry.decay_report()
        assert "ancient-skill.md" in out and "used-skill.md" not in out


class TestKGViz:
    def test_mermaid_export(self, tmp_path, monkeypatch):
        from suijin.modules.redteam.lib.intel import knowledge_graph as kg

        monkeypatch.setattr(kg, "GRAPH_PATH", tmp_path / "kg.json")
        kg._save({"t.example": {"waf": [{"rule": "blocks ' OR 1=1"}]}})
        out = kg.export_mermaid()
        assert out.startswith("graph LR") and "t.example" in out and "waf" in out


class TestExfil:
    def test_dns_tunneling(self):
        queries = [f"{'a8f3k2x9q7z4m1w5e8r2t6y0u' * 2}.exfil.example", "www.normal.example"]
        flags = exfil.detect_dns_tunneling(queries)
        assert len(flags) == 1 and "TUNNEL-LIKE" in flags[0]

    def test_beaconing(self):
        import random

        rnd = random.Random(7)
        events = [{"ip": "9.9.9.9", "ts": 1000 + i * 60 + rnd.uniform(-1, 1)} for i in range(10)]
        events += [{"ip": "8.8.8.8", "ts": 2000 + i * rnd.uniform(0, 60)} for i in range(10)]
        flags = exfil.detect_beaconing(events)
        assert len(flags) == 1 and "9.9.9.9" in flags[0] and "~60.0s" in flags[0]
