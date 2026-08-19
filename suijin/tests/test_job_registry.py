"""Phase 0, item 4 — ONE job registry.

Two registries existed: runtime.py defined _jobs/_job_lock (dead — exported
by dispatch but never populated), while the real jobs lived in
nodes/execute_tool_node.py, which tools/jobs.py reached into via private
attributes. The registry moves to tools/job_registry.py; the node and the
tools both delegate to it.
"""

import time

from suijin.modules.tools.lib import job_registry as jr


class TestJobRegistry:
    def test_spawn_and_lifecycle(self):
        job_id = jr.spawn("nmap_scan", {"target": "10.0.0.1"}, fn=lambda name, args, cfg: "scan done")
        deadline = time.time() + 5
        while time.time() < deadline:
            j = jr.get(job_id)
            if j["status"] == "done":
                break
            time.sleep(0.05)
        j = jr.get(job_id)
        assert j["status"] == "done"
        assert j["output"] == "scan done"
        assert "Job" in jr.status(job_id) and "done" in jr.status(job_id)
        assert "scan done" in jr.output(job_id)

    def test_failed_job(self):
        def boom(name, args, cfg):
            raise RuntimeError("exploded")

        job_id = jr.spawn("bad_tool", {}, fn=boom)
        deadline = time.time() + 5
        while time.time() < deadline and jr.get(job_id)["status"] != "failed":
            time.sleep(0.05)
        j = jr.get(job_id)
        assert j["status"] == "failed"
        assert "exploded" in j["error"]

    def test_get_missing(self):
        assert jr.get("nope") is None
        assert "not found" in jr.status("nope")

    def test_list_and_cancel(self):
        def slow(name, args, cfg):
            time.sleep(30)

        job_id = jr.spawn("slow_tool", {}, fn=slow)
        listed = jr.list_jobs()
        assert any(j["job_id"] == job_id for j in listed)
        assert jr.cancel(job_id) is True
        deadline = time.time() + 5
        while time.time() < deadline and jr.get(job_id)["status"] != "cancelled":
            time.sleep(0.05)
        assert jr.get(job_id)["status"] == "cancelled"
        jr.cancel(job_id)  # idempotent-ish: already finished
        assert jr.get(job_id)["status"] == "cancelled"

    def test_node_delegates_to_registry(self):
        """execute_tool_node must use THE registry — no private copy."""
        from suijin.modules.tools.lib import job_registry
        from suijin.nodes import execute_tool_node as node

        assert node._jobs is job_registry._jobs
        assert node._job_lock is job_registry._job_lock

    def test_jobs_tools_use_registry(self):
        """tools/jobs.py must not import node privates anymore."""
        import inspect

        from suijin.modules.tools.lib import jobs

        src = inspect.getsource(jobs)
        assert "execute_tool_node" not in src, "jobs.py still reaches into the node"

    def test_runtime_dead_registry_gone(self):
        """runtime.py's dead _jobs/_job_lock must no longer exist."""
        from suijin.tools import runtime as rt

        assert not hasattr(rt, "_jobs")
        assert not hasattr(rt, "_job_lock")

    def test_dispatch_reexports_alive(self):
        """dispatch's _jobs/_job_lock re-exports must point at THE registry."""
        from suijin.modules.tools.lib import dispatch as dp
        from suijin.modules.tools.lib import job_registry

        assert dp._jobs is job_registry._jobs
