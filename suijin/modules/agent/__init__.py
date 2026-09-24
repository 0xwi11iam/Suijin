"""Split shim — the real package lives at suijin.server.agent (the client/server split, phase B)."""

import sys as _sys

import suijin.server.agent as _real

_sys.modules[__name__] = _real
