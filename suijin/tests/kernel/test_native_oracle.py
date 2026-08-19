"""Core semantics — the kernel's resolve_dag and check_paths.

v4.1: the compiled Rust accelerator was retired; this suite (fixtures,
locked semantics, and the 300-tree fuzz properties) now pins the single
pure implementation directly. The fixtures and properties are unchanged
from the era when they ran head-to-head against the crate — they were
the safety net then and are the regression net now.
"""

import json
import random
import string

import pytest

from suijin.kernel import native


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
        pure = json.loads(native.resolve_dag(blob))
        assert pure["aborted"] is abort, f"{name}: {pure}"

    def test_semantics_locked(self):
        """Behavioral spot-checks independent of implementation."""
        blob = json.dumps(FIXTURES[1][1])
        out = json.loads(native.resolve_dag(blob))
        assert out["boot_order"] == ["solo"]

        blob = json.dumps(FIXTURES[2][1])  # chain
        out = json.loads(native.resolve_dag(blob))
        assert out["boot_order"] == ["a", "b", "c"]

        blob = json.dumps(FIXTURES[5][1])  # missing-deep
        out = json.loads(native.resolve_dag(blob))
        assert out["skipped"]["x"].startswith("missing dependency: ghost")
        assert "x" in out["skipped"]["y"].split() or out["skipped"]["y"].startswith("dependencies unavailable")

        blob = json.dumps(FIXTURES[11][1])  # collision
        out = json.loads(native.resolve_dag(blob))
        assert out["collisions"] == [["nmap", "installed"]]
        assert out["overridden"] == []

        blob = json.dumps(FIXTURES[12][1])  # override
        out = json.loads(native.resolve_dag(blob))
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


class TestFuzzProperties:
    def test_generated_trees(self):
        """300 random trees: resolves deterministically, never crashes."""
        rng = random.Random(20260818)
        for _ in range(300):
            tree = random_tree(rng, rng.randint(1, 12))
            blob = json.dumps(tree)
            first = json.loads(native.resolve_dag(blob))
            second = json.loads(native.resolve_dag(blob))
            assert first == second, f"nondeterministic on {blob}"

    def test_generated_paths(self):
        rng = random.Random(20260818)
        segments = ["a", "b", "..", ".", "c", "x.txt"]
        for _ in range(300):
            paths = ["/".join(rng.choices(segments, k=rng.randint(1, 5))) for _ in range(rng.randint(1, 6))]
            blob = json.dumps({"root": "/tmp/ws", "allow": ["/tmp/extra"], "paths": paths})
            verdicts = json.loads(native.check_paths(blob))
            # verdicts are keyed by path string (duplicates collapse)
            assert set(verdicts) == set(paths)


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
        verdicts = json.loads(native.check_paths(blob))
        assert isinstance(verdicts, dict) and verdicts
        assert json.loads(native.check_paths(blob)) == verdicts  # deterministic

    def test_verdicts_locked(self):
        blob = json.dumps(self.CASES[0])
        out = json.loads(native.check_paths(blob))
        assert out["a.txt"] and out["sub/b.txt"]
        assert not out["../esc"] and not out["/etc/x"] and not out["/tmp/other"]
        assert out["/tmp/ws/abs/ok"]


class TestCoreIdentity:
    def test_single_pure_implementation(self):
        """Retirement pin: no compiled core exists; the kernel runs the
        pure implementation and never looks for one."""
        assert native.source() == "pure-python"

    def test_resolve_on_core_only_tree(self):
        out = json.loads(native.resolve_dag(json.dumps([man("x", "core")])))
        assert out["boot_order"] == ["x"]
