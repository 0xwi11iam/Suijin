"""Kernel vfs, security, health, journal — final Phase 1 subsystem tests."""


import pytest

from suijin.kernel.health import HealthTracker
from suijin.kernel.journal import Journal
from suijin.kernel.security import PermissionSet, enforce
from suijin.kernel.vfs import Vfs


class TestVfs:
    def test_inside_workspace_allowed(self, tmp_path):
        vfs = Vfs(tmp_path)
        assert vfs.resolve("reports/x.md") == tmp_path / "reports" / "x.md"
        assert vfs.is_allowed("reports/x.md")

    def test_traversal_blocked(self, tmp_path):
        vfs = Vfs(tmp_path)
        assert not vfs.is_allowed("../../etc/passwd")
        assert not vfs.is_allowed("reports/../../../etc/passwd")

    def test_absolute_outside_blocked(self, tmp_path):
        vfs = Vfs(tmp_path)
        assert not vfs.is_allowed("/etc/passwd")

    def test_absolute_inside_allowed(self, tmp_path):
        vfs = Vfs(tmp_path)
        assert vfs.is_allowed(str(tmp_path / "ok.txt"))

    def test_symlink_escape_blocked(self, tmp_path):
        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "sneaky"
        if not link.exists():
            link.symlink_to(outside)
        vfs = Vfs(tmp_path)
        assert not vfs.is_allowed("sneaky/secret.txt")

    def test_tmp_allowlist(self, tmp_path):
        vfs = Vfs(tmp_path, allow=[tmp_path.parent / "tmpdata"])
        assert vfs.is_allowed(str(tmp_path.parent / "tmpdata" / "f"))
        assert not vfs.is_allowed(str(tmp_path.parent / "other" / "f"))


class TestSecurity:
    def test_permission_parsing(self):
        ps = PermissionSet.from_manifest(["network", "shell"])
        assert ps.has("network") and ps.has("shell")
        assert not ps.has("filesystem")

    def test_unknown_permission_rejected_at_parse(self):
        with pytest.raises(ValueError, match="unknown permission"):
            PermissionSet.from_manifest(["telepathy"])

    def test_enforce_allowed_and_denied(self):
        ps = PermissionSet.from_manifest(["network"])
        assert enforce(ps, "network", "nmap.scan") is None  # returns None when allowed
        denied = enforce(ps, "shell", "msf.run")
        assert denied is not None and "shell" in denied and "msf.run" in denied

    def test_empty_permissions_denies_everything(self):
        ps = PermissionSet.from_manifest([])
        assert enforce(ps, "network", "x") is not None


class TestHealthTracker:
    def test_record_and_get(self):
        h = HealthTracker()
        h.record_boot("platform", status="ok", detail="core loaded")
        entry = h.get("platform")
        assert entry["status"] == "ok" and "core loaded" in entry["detail"]

    def test_latest_wins(self):
        h = HealthTracker()
        h.record_boot("mod", status="ok")
        h.record_boot("mod", status="skipped", detail="dep gone")
        assert h.get("mod")["status"] == "skipped"

    def test_summary_counts(self):
        h = HealthTracker()
        h.record_boot("a", status="ok")
        h.record_boot("b", status="ok")
        h.record_boot("c", status="quarantined")
        s = h.summary()
        assert s["ok"] == 2 and s["quarantined"] == 1

    def test_missing_module(self):
        assert HealthTracker().get("ghost") is None


class TestJournal:
    def test_append_and_tail(self, tmp_path):
        j = Journal(tmp_path / "logs", ring_size=10)
        j.append("boot", "system started")
        j.append("module.start", "platform up")
        lines = j.tail(2)
        assert len(lines) == 2 and "system started" in lines[0]

    def test_ring_bounds_memory(self, tmp_path):
        j = Journal(tmp_path / "logs", ring_size=5)
        for i in range(20):
            j.append("tick", str(i))
        assert len(j.tail(100)) == 5

    def test_persisted_to_disk(self, tmp_path):
        j = Journal(tmp_path / "logs")
        j.append("boot", "hello")
        j.flush()
        files = list((tmp_path / "logs").glob("*.log"))
        assert files and "hello" in files[0].read_text()

    def test_rotation(self, tmp_path):
        d = tmp_path / "logs"
        d.mkdir(parents=True)
        (d / "journal.log").write_text("x" * 2048)  # pre-existing oversized
        j = Journal(d, ring_size=10, max_bytes=1024)
        j.append("boot", "y" * 2000)
        j.flush()
        logs = sorted(d.glob("journal*.log"))
        assert len(logs) >= 2  # rotated
