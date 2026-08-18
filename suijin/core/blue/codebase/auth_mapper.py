"""Auth mapper — identify auth guards per endpoint."""

from __future__ import annotations

import re
from pathlib import Path


def map_auth(root: Path, endpoints: list) -> list:
    for ep in endpoints:
        fp = Path(ep.get("file", ""))
        if not fp.exists():
            ep["auth"] = "unknown"
            continue
        try:
            src = fp.read_text(errors="ignore")
            ctx = "\n".join(src.split("\n")[max(0, ep.get("line", 0) - 30) : ep.get("line", 0)])
            if re.search(
                r"@login_required|@jwt_required|@auth_required|authenticate|passport\.authenticate|requireAuth", ctx
            ):
                ep["auth"] = "authenticated"
            elif re.search(r"@public|@noauth|allow_anonymous", ctx):
                ep["auth"] = "public"
            else:
                ep["auth"] = "none"
        except Exception:
            import logging

            logging.getLogger("suijin").warning("Auth mapping failed", exc_info=True)
            ep["auth"] = "unknown"
    return endpoints
