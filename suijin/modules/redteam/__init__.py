"""Split shim — the real package lives at suijin.server.redteam (the client/server split, phase B)."""

import sys as _sys

import suijin.server.redteam as _real

_sys.modules[__name__] = _real
