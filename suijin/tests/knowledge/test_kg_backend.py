"""KG backends — JSON default (parity) + Neo4j theory (mocked driver).

The switch contract: same call, same result shape, config flips the
backend. The Neo4j driver is optional — selection fails safe to JSON
with a logged error, never bricking an engagement.
"""

from unittest import mock

from suijin.modules.redteam.lib.intel import kg_backend as kb
from suijin.modules.redteam.lib.intel import knowledge_graph as kg


class TestJsonParity:
    def test_full_cycle_through_the_shim(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kg, "GRAPH_PATH", tmp_path / "kg.json")
        kg.add_constraint("t.example", "blocks", "' OR 1=1", evidence="403 waf", confidence=0.9)
        kg.add_constraint("t.example", "blocks", "' OR 1=1", evidence="again", confidence=0.5)  # dedup, conf ratchets
        cons = kg.get_constraints("t.example")
        assert len(cons["blocks"]) == 1
        assert cons["blocks"][0]["confidence"] == 0.9  # max kept, not overwritten
        assert kg.check_payload("t.example", "x' OR 1=1--")["blocked"] is True
        assert kg.check_payload("t.example", "clean payload")["blocked"] is False
        assert "t.example" in kg.get_all_targets()
        assert "blocks" in kg.summary("t.example")
        assert "t.example" in kg.export_mermaid()
        kg.clear_target("t.example")
        assert kg.get_all_targets() == []


class _FakeRecord(dict):
    def data(self):
        return dict(self)


class _FakeResult(list):
    pass


class _FakeSession:
    def __init__(self, script):
        self.script = script
        self.queries = []

    def run(self, query, **params):
        self.queries.append((query, params))
        return self.script.pop(0) if self.script else _FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeDriver:
    def __init__(self, script):
        self._script = script
        self.session_obj = _FakeSession(script)
        self.closed = False

    def session(self):
        return self.session_obj

    def verify_connectivity(self):
        pass

    def close(self):
        self.closed = True


class TestNeo4jBackend:
    def _kg(self, script):
        # inject the fake driver directly — _connect() short-circuits on
        # an existing _driver (its real fast path), no class-mock lifetime
        driver = _FakeDriver(script)
        kgx = kb.Neo4jKG("bolt://localhost:7687", "neo4j", "pw")
        kgx._driver = driver
        return kgx, driver

    def test_add_constraint_merge_semantics(self):
        kgx, driver = self._kg([])
        kgx.add_constraint("t.example", "blocks", "' OR 1=1", evidence="403", confidence=0.9)
        q, p = driver.session_obj.queries[0]
        assert "MERGE (t:Target {name: $target})" in q
        assert "MERGE (t)-[:HAS]->(c:Constraint {rule: $rule, ctype: $ctype})" in q
        assert "ON MATCH SET" in q and "THEN $confidence ELSE c.confidence END" in q
        assert p == {"target": "t.example", "ctype": "blocks", "rule": "' OR 1=1", "evidence": "403", "confidence": 0.9}

    def test_get_constraints_returns_json_shape(self):
        rows = _FakeResult(
            [
                _FakeRecord(
                    {
                        "ctype": "blocks",
                        "rule": "' OR 1=1",
                        "evidence": "403",
                        "confidence": 0.9,
                        "verified_at": "2026-08-21",
                        "last_seen": "2026-08-21",
                    }
                ),
                _FakeRecord(
                    {
                        "ctype": "verified_cve",
                        "rule": "CVE-2021-44228",
                        "evidence": "rce",
                        "confidence": 1.0,
                        "verified_at": "2026-08-21",
                        "last_seen": "2026-08-21",
                    }
                ),
            ]
        )
        kgx, _ = self._kg([rows])
        out = kgx.get_constraints("t.example")
        assert set(out) == {"blocks", "verified_cve"}
        assert out["blocks"][0]["rule"] == "' OR 1=1"
        assert out["verified_cve"][0]["confidence"] == 1.0
        # shape parity: these drive check_payload/summary unchanged
        assert kg.check_payload.__doc__  # api present
        cons_shape = {k: sorted(v[0].keys()) for k, v in out.items()}
        assert cons_shape["blocks"] == sorted(["rule", "evidence", "confidence", "verified_at", "last_seen"])

    def test_clear_target_detaches_then_sweeps_orphans(self):
        kgx, driver = self._kg([_FakeResult([]), _FakeResult([])])
        kgx.clear_target("t.example")
        q1, p1 = driver.session_obj.queries[0]
        q2, _ = driver.session_obj.queries[1]
        assert "DETACH DELETE t" in q1 and p1["target"] == "t.example"
        assert "NOT ()-[:HAS]->(c)" in q2 and "DELETE c" in q2

    def test_get_all_targets(self):
        kgx, _ = self._kg([_FakeResult([_FakeRecord({"name": "a"}), _FakeRecord({"name": "b"})])])
        assert kgx.get_all_targets() == ["a", "b"]


class TestBackendSelection:
    def test_default_is_json(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SUIJIN_KG_BACKEND", raising=False)
        monkeypatch.setattr(kb, "_config_backend", lambda: "json")
        b = kb.get_backend(lambda: tmp_path / "kg.json")
        assert isinstance(b, kb.JsonKG)

    def test_env_switch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SUIJIN_KG_BACKEND", "neo4j")
        monkeypatch.setenv("SUIJIN_NEO4J_URI", "bolt://localhost:7687")
        fake = _FakeDriver([])
        with mock.patch.object(kb.Neo4jKG, "_connect", return_value=fake):
            b = kb.get_backend(lambda: tmp_path / "kg.json")
        assert isinstance(b, kb.Neo4jKG)

    def test_unreachable_neo4j_falls_back_to_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SUIJIN_KG_BACKEND", "neo4j")
        monkeypatch.setenv("SUIJIN_NEO4J_URI", "bolt://localhost:7687")

        def boom(self):
            raise ConnectionError("refused")

        with mock.patch.object(kb.Neo4jKG, "_connect", boom):
            b = kb.get_backend(lambda: tmp_path / "kg.json")
        assert isinstance(b, kb.JsonKG)  # engagement never bricks

    def test_neo4j_without_uri_stays_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SUIJIN_KG_BACKEND", "neo4j")
        monkeypatch.delenv("SUIJIN_NEO4J_URI", raising=False)
        monkeypatch.setattr(kb, "_config_uri", lambda: "")
        b = kb.get_backend(lambda: tmp_path / "kg.json")
        assert isinstance(b, kb.JsonKG)

    def test_missing_driver_package_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SUIJIN_KG_BACKEND", "neo4j")
        monkeypatch.setenv("SUIJIN_NEO4J_URI", "bolt://x")

        def no_driver(self):
            raise ImportError("No module named 'neo4j'")

        with mock.patch.object(kb.Neo4jKG, "_connect", no_driver):
            b = kb.get_backend(lambda: tmp_path / "kg.json")
        assert isinstance(b, kb.JsonKG)

    def test_status_line(self, monkeypatch):
        monkeypatch.delenv("SUIJIN_KG_BACKEND", raising=False)
        monkeypatch.setattr(kb, "_config_backend", lambda: "json")
        monkeypatch.setattr(kb, "_config_uri", lambda: "")
        assert kb.backend_status().startswith("json (default)")
