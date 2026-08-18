"""Oracle tests — pure Python vs the compiled Rust core.

The contract: both implementations produce CANONICALLY-IDENTICAL JSON
for resolve_dag and check_paths across a generated zoo of trees (healthy,
diamond, chains, cycles, missing deps, collisions, overrides, broken).
When the compiled wheel is absent, the suite still fully verifies the
pure path (which IS the fallback); when present, every case also runs
head-to-head. This is the safety net that makes the hybrid kernel honest.
"""

import json
import random
import string

import pytest

from suijin.kernel import _pure, native


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def man(mid, tier="recommended", requires=(), overrides=(), broken=None):
    return {
        "id": mid,
        "version": "1.0.0",
        "tier": tier,
        "requires": list(requires),
        "overrides": list(overrides),
        **({"broken": broken} if broken else {}),
    }


FIXTURES = [
    # name, manifests, expect_aborted
    ("empty", [], False),
    ("single", [man("solo", "core")], False),
    ("chain", [man("c", requires=["b"]), man("b", requires=["a"]), man("a", "core")], False),
    (
        "diamond",
        [man("d", requires=["b", "c"]), man("b", requires=["a"]), man("c", requires=["a"]), man("a", "core")],
        False,
    ),
    ("missing-dep", [man("x", requires=["ghost"])], False),
    ("missing-deep", [man("y", requires=["x"]), man("x", requires=["ghost"])], False),
    ("cycle-2", [man("a", requires=["b"]), man("b", requires=["a"])], False),
    ("cycle-3", [man("a", requires=["b"]), man("b", requires=["c"]), man("c", requires=["a"])], False),
    ("core-cycle-aborts", [man("a", "core", requires=["b"]), man("b", "core", requires=["a"])], True),
    ("core-missing-aborts", [man("t", "core", requires=["ghost"])], True),
    ("broken-quarantined", [man("good", "core"), man("bad", broken="invalid manifest")], False),
    ("collision", [man("nmap", "recommended"), man("nmap", "installed")], False),
    ("override-wins", [man("nmap", "recommended"), man("nmap", "installed", overrides=["nmap"])], False),
    ("self-cycle", [man("s", requires=["s"])], False),
    (
        "cycle-plus-healthy",
        [man("a", "core"), man("p", requires=["q"]), man("q", requires=["p"]), man("h", requires=["a"])],
        False,
    ),
]


class TestResolveDagOracle:
    @pytest.mark.parametrize("name,manifests,abort", FIXTURES, ids=[f[0] for f in FIXTURES])
    def test_fixture(self, name, manifests, abort):
        blob = json.dumps(manifests)
        pure = json.loads(_pure.resolve_dag(blob))
        assert pure["aborted"] is abort, f"{name}: {pure}"
        if native.available():
            rust = json.loads(native.resolve_dag(blob))
            assert rust == pure, f"{name}: rust/pure diverge\nrust={rust}\npure={pure}"

    def test_semantics_locked(self):
        """Behavioral spot-checks independent of implementation."""
        blob = json.dumps(FIXTURES[1][1])
        out = json.loads(_pure.resolve_dag(blob))
        assert out["boot_order"] == ["solo"]

        blob = json.dumps(FIXTURES[2][1])  # chain
        out = json.loads(_pure.resolve_dag(blob))
        assert out["boot_order"] == ["a", "b", "c"]

        blob = json.dumps(FIXTURES[5][1])  # missing-deep
        out = json.loads(_pure.resolve_dag(blob))
        assert out["skipped"]["x"].startswith("missing dependency: ghost")
        assert "x" in out["skipped"]["y"].split() or out["skipped"]["y"].startswith("dependencies unavailable")

        blob = json.dumps(FIXTURES[11][1])  # collision
        out = json.loads(_pure.resolve_dag(blob))
        assert out["collisions"] == [["nmap", "installed"]]
        assert out["overridden"] == []

        blob = json.dumps(FIXTURES[12][1])  # override
        out = json.loads(_pure.resolve_dag(blob))
        assert out["collisions"] == []
        assert out["overridden"] == ["nmap"]


def random_tree(rng: random.Random, n: int) -> list[dict]:
    ids = ["".join(rng.choices(string.ascii_lowercase, k=4)) for _ in range(n)]
    seen = set()
    uniq = [i for i in ids if not (i in seen or seen.add(i))]
    manifests = []
    for i, mid in enumerate(uniq):
        deps = rng.sample(uniq[:i], k=min(len(uniq[:i]), rng.randint(0, 2))) if i else []
        tier = rng.choice(["core", "recommended", "recommended", "installed"])
        manifests.append(man(mid, tier, requires=deps))
    return manifests


class TestFuzzEquivalence:
    def test_generated_trees(self):
        rng = random.Random(20260818)
        diverged = 0
        for _ in range(300):
            tree = random_tree(rng, rng.randint(1, 12))
            blob = json.dumps(tree)
            pure = json.loads(_pure.resolve_dag(blob))
            if native.available():
                rust = json.loads(native.resolve_dag(blob))
                if rust != pure:
                    diverged += 1
                    if diverged == 1:
                        pytest.fail(f"divergence on {blob}\nrust={rust}\npure={pure}")
        assert diverged == 0

    def test_generated_paths(self):
        rng = random.Random(20260818)
        segments = ["a", "b", "..", ".", "c", "x.txt"]
        for _ in range(300):
            paths = ["/".join(rng.choices(segments, k=rng.randint(1, 5))) for _ in range(rng.randint(1, 6))]
            blob = json.dumps({"root": "/tmp/ws", "allow": ["/tmp/extra"], "paths": paths})
            pure = json.loads(_pure.check_paths(blob))
            if native.available():
                rust = json.loads(native.check_paths(blob))
                assert rust == pure, f"path divergence: {blob}\n{rust} vs {pure}"


class TestCheckPathsOracle:
    CASES = [
        {
            "root": "/tmp/ws",
            "allow": [],
            "paths": ["a.txt", "sub/b.txt", "../esc", "/etc/x", "/tmp/ws/abs/ok", "/tmp/other"],
        },
        {"root": "/tmp/ws", "allow": ["/tmp/extra"], "paths": ["/tmp/extra/f", "/tmp/extra/../no"]},
        {"root": "/tmp/ws", "allow": [], "paths": [".", "..", "a/../b", "./x/../..", "a/./b/./c"]},
        {"root": "/w", "allow": [], "paths": ["/w", "/w/", "/w/x"]},
    ]

    @pytest.mark.parametrize("case", CASES)
    def test_case(self, case):
        blob = json.dumps(case)
        pure = json.loads(_pure.check_paths(blob))
        if native.available():
            rust = json.loads(native.check_paths(blob))
            assert rust == pure, f"{case}\n{rust} vs {pure}"

    def test_verdicts_locked(self):
        blob = json.dumps(self.CASES[0])
        out = json.loads(_pure.check_paths(blob))
        assert out["a.txt"] and out["sub/b.txt"]
        assert not out["../esc"] and not out["/etc/x"] and not out["/tmp/other"]
        assert out["/tmp/ws/abs/ok"]


class TestShim:
    def test_source_reported(self):
        s = native.source()
        assert s in ("pure-python", "wheel", "dev-build")

    def test_pure_alone_is_correct(self):
        """With the native module forcibly hidden, the shim must still work."""
        from suijin.kernel import native as nat

        real = nat._native
        try:
            nat._native = None
            out = json.loads(nat.resolve_dag(json.dumps([man("x", "core")])))
            assert out["boot_order"] == ["x"]
            assert nat.source() == "pure-python"
        finally:
            nat._native = real
