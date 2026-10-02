"""Phase 4 integration — disabled modules vanish from a REAL boot."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MODULES = REPO / "suijin" / "modules"
SERVER = REPO / "suijin" / "server"  # the split: first-party homes


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    from suijin.modules import manager as mgmt

    monkeypatch.setattr(mgmt, "STATE_DIR", tmp_path / ".suijin")
    monkeypatch.setattr(mgmt, "USER_MODULES", tmp_path / ".suijin" / "modules")
    return mgmt
