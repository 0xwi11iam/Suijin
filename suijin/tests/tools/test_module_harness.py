"""F44 — the pack test harness."""

from suijin.modules.tools.lib import module_sdk


class TestHarness:
    def test_healthy_pack_passes(self):
        ok, lines = module_sdk.test_pack("encodesk")
        assert ok, lines
        joined = "\n".join(lines)
        assert "boots as kernel unit" in joined and "advertised in the catalog" in joined

    def test_missing_pack(self):
        ok, lines = module_sdk.test_pack("no_such_pack_xyz")
        assert not ok and any("no pack directory" in ln for ln in lines)

    def test_vendored_spot_checks(self):
        for name in ("nmap", "portmap"):
            ok, lines = module_sdk.test_pack(name)
            assert ok, f"{name}: {lines}"
