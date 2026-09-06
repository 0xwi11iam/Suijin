"""Engage worker — runs one engagement detached, launched by the gateway.

Separate process on purpose: the gateway stays responsive; the worker
owns the whole engagement lifecycle (boot -> run -> audit/save) and
exits. All its output lands in the workspace artifacts the /events
stream already tails — the desktop app sees the engagement live
without any extra plumbing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--objective", required=True)
    ap.add_argument("--target", default="")
    ap.add_argument("--config", default="{}")
    args = ap.parse_args()

    try:
        cfg = json.loads(args.config or "{}")
        if not isinstance(cfg, dict):
            raise ValueError("config must be a JSON object")
    except Exception as e:  # noqa: BLE001 — stderr is DEVNULL'd: write the
        # crash into the workspace so /events and the operator can see WHY
        # the worker died (the old silent-death-behind-DEVNULL)
        try:
            from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

            d = WORKSPACE_DIR / "outputs" / "logs"
            d.mkdir(parents=True, exist_ok=True)
            (d / "engage_worker_crash.log").open("a").write(f"bad --config payload: {type(e).__name__}: {e}\n")
        except Exception:  # noqa: BLE001
            pass
        return 2

    from suijin.modules.platform.lib.runtime import init_runtime

    init_runtime()

    from suijin.modules.redteam.lib.redteamer import run_red_team_async

    async def _run():
        await run_red_team_async(cfg, args.objective)

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
