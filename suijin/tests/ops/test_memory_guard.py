"""The memory guard's decisions are pure and must stay that way."""

from suijin.server.platform.lib.memory_guard import evaluate, limit_for


def test_evaluate_thresholds():
    assert evaluate(100, 1000) == "ok"
    assert evaluate(799, 1000) == "ok"
    assert evaluate(800, 1000) == "warn"
    assert evaluate(999, 1000) == "warn"
    assert evaluate(1000, 1000) == "kill"
    assert evaluate(5000, 0) == "ok"  # no limit configured


def test_limit_for_roles(monkeypatch):
    monkeypatch.delenv("SUIJIN_MEMORY_LIMIT_MB", raising=False)
    assert limit_for("agent") == 1800
    assert limit_for("coordinator") == 1200
    assert limit_for("unknown") == 1500
    monkeypatch.setenv("SUIJIN_MEMORY_LIMIT_MB", "4096")
    assert limit_for("agent") == 4096
    monkeypatch.setenv("SUIJIN_MEMORY_LIMIT_MB", "nonsense")
    assert limit_for("agent") == 1800


def test_guard_is_inert_under_pytest():
    from suijin.server.platform.lib import memory_guard

    # pytest sets PYTEST_CURRENT_TEST at test time — start must decline.
    assert memory_guard.start_guard("agent") is False
