"""The active-run event-bus registry (suijin/server/__init__.py).

External surfaces (gateway live-stream, TUI adapter) must find the
CURRENT run's bus without owning it. The registry is a single slot with
identity-guarded clear so a late `finally` from an old run can never
clear a newer run's bus.
"""

from __future__ import annotations

from suijin.server import (
    EventBus,
    active_bus,
    clear_active_bus,
    register_active_bus,
)


def _reset():
    for _ in range(3):
        b = active_bus()
        clear_active_bus(b) if b is not None else None


class TestActiveBusRegistry:
    def test_idle_is_none(self):
        _reset()
        assert active_bus() is None

    def test_register_live_clear_idle(self):
        _reset()
        bus = EventBus()
        register_active_bus(bus)
        assert active_bus() is bus
        clear_active_bus(bus)
        assert active_bus() is None

    def test_replacement_last_wins(self):
        _reset()
        b1, b2 = EventBus(), EventBus()
        register_active_bus(b1)
        register_active_bus(b2)
        assert active_bus() is b2
        clear_active_bus(b1)  # old run's late finally — must NOT clear b2
        assert active_bus() is b2
        clear_active_bus(b2)
        assert active_bus() is None

    def test_unrelated_bus_clear_is_noop(self):
        _reset()
        live, stray = EventBus(), EventBus()
        register_active_bus(live)
        clear_active_bus(stray)
        assert active_bus() is live
        clear_active_bus(live)


def test_bus_publish_fans_registered_subscribers():
    bus = EventBus()
    seen = []
    bus.subscribe(lambda rec: seen.append(rec["kind"]))
    bus.publish("session.start", objective="x")
    bus.publish("iteration", n=1)
    assert seen == ["session.start", "iteration"]
    assert bus.subscriber_count == 1
