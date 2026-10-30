"""/connect pinning — the two live-debug bugs (2026-10-30), held by
source contract so they cannot silently return.
"""

import re
from pathlib import Path

import suijin.server.agent.lib.mesh as mesh

SRC = Path(mesh.__file__).read_text(encoding="utf-8")


class TestConnectSourceContracts:
    def test_no_retired_workspace_default(self):
        """The remote discovery must NOT export the retired v7.2 path —
        every healthy remote read a ghost registry and discovery failed."""
        inner = re.search(r'inner = \(\s*f?"{ws_prefix}"?.*?mesh-port', SRC, re.S)
        assert inner, "inner command block not found"
        block = inner.group(0)
        assert ".suijin/workspace" not in block
        assert "ws_prefix" in block  # export only when explicitly given

    def test_retry_tunnel_slot_order(self):
        """cmd[-4] is the -L slot (to THEIR server), cmd[-2] is -R (back to
        ours) — the swap wired HELLOs into loops and self-registered."""
        m = re.search(r"cmd\[-4\]\s*=\s*f\"127\.0\.0\.1:\{lp\}:127\.0\.0\.1:\{int\(theirs\['port'\]\)\}\"", SRC)
        assert m, "-L slot must carry lp:their-port"
        m2 = re.search(r"cmd\[-2\]\s*=\s*f\"127\.0\.0\.1:\{rp\}:127\.0\.0\.1:\{srv\.port\}\"", SRC)
        assert m2, "-R slot must carry rp:our-port"

    def test_wrong_peer_guard_present(self):
        assert "answered by THIS process" in SRC


class TestSpecParsing:
    def test_host_port_split(self):
        # connect_remote's parsing is inline; pin the behavior through the
        # usage check at least — user@host:2222 parses to spec + port
        import inspect

        src = inspect.getsource(mesh.connect_remote)
        assert "rpartition" in src and "isdigit" in src
