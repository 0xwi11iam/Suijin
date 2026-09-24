"""Split shim — the real package lives at suijin.server.ops (the client/server split, phase B)."""

import sys as _sys

import suijin.server.ops as _real

_sys.modules[__name__] = _real
