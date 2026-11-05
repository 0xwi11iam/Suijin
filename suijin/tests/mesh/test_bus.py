"""The swarm bus — TCP pub/sub replacing gc.log (2026-11-05).

Broker: in-memory topic routing, one connection per subscriber, zero
file I/O for communication. Client: persistent connection, auto-reconnect,
drain-based consumption.
"""

import time

import pytest

from suijin.server.mesh.bus import BusBroker, BusClient, topic_matches


class TestTopicMatching:
    def test_exact(self):
        assert topic_matches("/engagement/foo/findings", "/engagement/foo/findings")

    def test_single_segment_wildcard(self):
        assert topic_matches("/engagement/*/findings", "/engagement/foo/findings")
        assert not topic_matches("/engagement/*/findings", "/engagement/foo/facts")

    def test_double_wildcard(self):
        assert topic_matches("/engagement/**", "/engagement/foo/bar/baz")

    def test_no_match(self):
        assert not topic_matches("/swarm/status", "/engagement/foo/findings")


class TestBusBroker:
    @pytest.fixture()
    def broker(self):
        b = BusBroker()
        b.start()
        yield b
        b.stop()

    def test_pub_sub_end_to_end(self, broker):
        """One subscriber receives a matching message — the core test."""
        client = BusClient()
        assert client.connect("127.0.0.1", broker.port, "test-1")
        client.subscribe("/engagement/*/findings")

        # give the broker a moment to register the subscription
        time.sleep(0.1)

        # publish from the same client (the broker routes it back)
        client.publish("/engagement/test-run/findings", {"finding": "XSS on /login", "severity": "high"})

        # the message should arrive
        deadline = time.time() + 2
        msgs = []
        while time.time() < deadline:
            msgs = client.drain()
            if msgs:
                break
            time.sleep(0.05)

        assert len(msgs) >= 1
        assert msgs[0]["topic"] == "/engagement/test-run/findings"
        assert msgs[0]["payload"]["finding"] == "XSS on /login"
        client.close()

    def test_non_matching_topic_not_delivered(self, broker):
        client = BusClient()
        client.connect("127.0.0.1", broker.port, "test-2")
        client.subscribe("/swarm/status")

        time.sleep(0.1)
        client.publish("/engagement/other/findings", {"finding": "should not arrive"})

        time.sleep(0.2)
        msgs = client.drain()
        # the PUB is not delivered back (it went to /engagement, not /swarm)
        assert all(m.get("topic", "").startswith("/swarm") for m in msgs)
        client.close()

    def test_two_subscribers_both_receive(self, broker):
        c1 = BusClient()
        c2 = BusClient()
        c1.connect("127.0.0.1", broker.port, "a")
        c2.connect("127.0.0.1", broker.port, "b")
        c1.subscribe("/test/**")
        c2.subscribe("/test/**")

        time.sleep(0.1)
        # use a third client to publish
        c3 = BusClient()
        c3.connect("127.0.0.1", broker.port, "pub")
        c3.publish("/test/hello", {"msg": "both should get this"})

        deadline = time.time() + 2
        m1, m2 = [], []
        while time.time() < deadline:
            m1 = c1.drain()
            m2 = c2.drain()
            if m1 and m2:
                break
            time.sleep(0.05)

        assert m1 and any(m.get("payload", {}).get("msg") == "both should get this" for m in m1)
        assert m2 and any(m.get("payload", {}).get("msg") == "both should get this" for m in m2)
        for c in (c1, c2, c3):
            c.close()

    def test_broker_stats(self, broker):
        client = BusClient()
        client.connect("127.0.0.1", broker.port, "stats-test")
        time.sleep(0.1)

        stats = broker.stats
        assert stats["subscribers"] >= 1
        client.close()


class TestBusPerformance:
    def test_throughput(self):
        """1000 messages through the bus — should complete in under 2s.
        (The gc.log equivalent: 1000 file opens + reads + appends.)"""
        broker = BusBroker()
        broker.start()
        try:
            sub = BusClient()
            sub.connect("127.0.0.1", broker.port, "perf-sub")
            sub.subscribe("/perf/**")
            time.sleep(0.1)

            pub = BusClient()
            pub.connect("127.0.0.1", broker.port, "perf-pub")

            start = time.time()
            for i in range(1000):
                pub.publish("/perf/batch", {"n": i})
            elapsed = time.time() - start

            # let the reader catch up
            time.sleep(0.5)
            msgs = sub.drain(2000)

            assert elapsed < 2.0, f"1000 publishes took {elapsed:.2f}s"
            assert len(msgs) >= 900  # some may be in flight
            sub.close()
            pub.close()
        finally:
            broker.stop()
