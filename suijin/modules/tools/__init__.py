"""Split shim — the real package lives at suijin.server.tools (the client/server split, phase B)."""

import sys as _sys

import suijin.server.tools as _real

_sys.modules[__name__] = _real
