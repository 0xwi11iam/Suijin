"""Phase 3 — recommended tier: providers, redteam, knowledge, ops.

The full OS boot: 7 core + 5 recommended modules in one DAG, console
hooks populated by mode modules, disable-means-disappear at tier level.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MODULES = REPO / "suijin" / "modules"
SERVER = REPO / "suijin" / "server"  # the split: first-party homes


def boot_all(tmp_path):
    from suijin.kernel import controller

    return controller.boot(module_roots=[SERVER, MODULES], workspace=tmp_path, quiet=True)


class TestQuietBootWithRecommended:
    def test_healthy_full_boot_is_silent(self, tmp_path, capsys):
        ctx, report = boot_all(tmp_path)
        assert capsys.readouterr().out == ""  # quiet: healthy => silent
        assert not report.skipped and not report.quarantined
        ctx.shutdown()
