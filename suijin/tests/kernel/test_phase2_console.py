"""Phase 2 — console module: feature-blind core with extension hooks.

Console owns the SURFACES (menus, verbs), not the features: modules
register menu entries and CLI verbs as hooks on the Context, and console
renders whatever is registered. A disabled module's entries genuinely
disappear — that's the proof the architecture is real.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MODULES = REPO / "suijin" / "modules"
SERVER = REPO / "suijin" / "server"  # the split: first-party homes
