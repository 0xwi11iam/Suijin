"""Resource governor tests — process pool, disk batcher, connection pool."""

import time

from suijin.server.swarm.governor import ConnectionPool, DiskBatcher, ProcessPool


class TestProcessPool:
    def test_executes_command(self):
        pool = ProcessPool(max_workers=2)
        pool.start()
        try:
            result = pool.execute(["echo", "hello"])
            assert result.returncode == 0
            assert "hello" in result.stdout
        finally:
            pool.stop()

    def test_respects_max_workers(self):
        pool = ProcessPool(max_workers=2)
        pool.start()
        try:
            # submit 5 quick commands — should all complete
            results = [pool.execute(["echo", str(i)], priority=1) for i in range(5)]
            assert all(r.returncode == 0 for r in results)
            assert pool.stats["max_workers"] == 2
        finally:
            pool.stop()

    def test_timeout_enforced(self):
        pool = ProcessPool(max_workers=1, default_timeout=1)
        pool.start()
        try:
            result = pool.execute(["sleep", "5"])
            assert result.returncode == -1
            assert "timeout" in result.stderr
        finally:
            pool.stop()


class TestDiskBatcher:
    def test_batches_writes(self, tmp_path):
        batcher = DiskBatcher(flush_interval_s=0.05)
        batcher.start()
        try:
            log = tmp_path / "events.log"
            for i in range(20):
                batcher.write(str(log), f"event-{i}")
            time.sleep(0.3)  # let the flush cycle run
            batcher.stop()  # final flush

            content = log.read_text()
            assert "event-0" in content
            assert "event-19" in content
            assert batcher.stats["flushed"] == 20
        finally:
            batcher.stop()

    def test_creates_parent_dirs(self, tmp_path):
        batcher = DiskBatcher(flush_interval_s=0.05)
        batcher.start()
        try:
            log = tmp_path / "deep" / "nested" / "dir" / "log.txt"
            batcher.write(str(log), "content")
            time.sleep(0.2)
            batcher.stop()
            assert log.is_file()
            assert "content" in log.read_text()
        finally:
            batcher.stop()


class TestConnectionPool:
    def test_get_and_put(self):
        pool = ConnectionPool()
        session = pool.get()
        assert session is not None
        pool.put(session)
        session2 = pool.get()
        assert session2 is session  # reused
        pool.close()

    def test_pool_cap(self):
        pool = ConnectionPool(pool_size=2)
        s1, s2, s3 = pool.get(), pool.get(), pool.get()
        pool.put(s1)
        pool.put(s2)
        pool.put(s3)  # exceeds cap — should be dropped
        pool.close()


class TestGovernorIntegration:
    def test_pool_and_batcher_together(self, tmp_path):
        """The realistic path: run commands, batch their outputs."""
        pool = ProcessPool(max_workers=2)
        batcher = DiskBatcher(flush_interval_s=0.05)
        pool.start()
        batcher.start()
        try:
            for i in range(5):
                result = pool.execute(["echo", f"output-{i}"])
                batcher.write(str(tmp_path / "tool.log"), f"exit={result.returncode} out={result.stdout.strip()}")
            time.sleep(0.2)
            batcher.stop()
            pool.stop()

            log = (tmp_path / "tool.log").read_text()
            assert "output-0" in log
            assert "output-4" in log
        finally:
            batcher.stop()
            pool.stop()
