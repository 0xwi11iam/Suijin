"""worker — background jobs (no listener; pulls from the queue dir).

Planted here:
  r3  job-claim is exists-check-then-rename with a sleep in between —
      two rapid identical jobs (core's dedupe race) both process; the
      second processing appends the race flag line.
  CHAIN-2's sink — a job's `template` field renders through Jinja2 with
      no sandbox (SSTI → command execution → the worker env flag file).

The crown flag lives in the worker's ENV FILE (/tmp/northbridge/
worker-env.txt), readable only through the SSTI's file read.
"""

from __future__ import annotations

import json
import os
import time

from suijin.lab.northbridge import ROOT, emit

QUEUE = os.path.join(ROOT, "queue")
CLAIMED = os.path.join(ROOT, "claimed")
OUT = os.path.join(ROOT, "exports")
ENV_FILE = os.path.join(ROOT, "worker-env.txt")

ENV_BODY = """NORTHBRIDGE_WORKER_ID=wrk-1
NORTHBRIDGE_DB=/tmp/northbridge/core.db
NORTHBRIDGE_FLAG=FLAG{northbridge_rce_crown}
"""


def seed() -> None:
    for d in (QUEUE, CLAIMED, OUT):
        os.makedirs(d, exist_ok=True)
    if not os.path.isfile(ENV_FILE):
        with open(ENV_FILE, "w") as f:
            f.write(ENV_BODY)


def _render(jid: str, template: str) -> str:
    """CHAIN-2's sink. The spec (internal-job-spec.json) documents the
    template field; rendering is UNSANDBOXED Jinja — the SSTI."""
    from jinja2 import Environment

    env = Environment()  # no sandbox — the planted flaw
    try:
        out = env.from_string(template).render(report_title=jid, source="worker")
        emit("e_ssti_sink", jid)
        return out
    except Exception as e:  # noqa: BLE001 — render failures are job failures
        return f"render error: {e}"


def _process(job: dict) -> str:
    template = job.get("template")
    if template:
        return _render(str(job["id"]), str(template))
    # ordinary export jobs (r3's double-processing carries the flag)
    return f"export {job['name']} for {job['tenant']} complete"


def _claim_loop_once() -> None:
    for f in sorted(os.listdir(QUEUE)):
        if not f.endswith(".json"):
            continue
        src = os.path.join(QUEUE, f)
        dst = os.path.join(CLAIMED, f)
        # r3: exists-check then rename, with processing time in between
        if os.path.exists(dst):
            continue
        job = json.loads(open(src, encoding="utf-8").read())
        os.rename(src, dst)  # claim
        time.sleep(0.05)  # the race window: a twin job lands here
        # a twin with the same NAME double-processing = the race won
        twins = [
            g
            for g in os.listdir(CLAIMED)
            if g.endswith(".json") and json.loads(open(os.path.join(CLAIMED, g), encoding="utf-8").read()).get("name")
            == job.get("name")
        ]
        out = _process(job)
        if len(twins) > 1:
            emit("r3_worker_race", job.get("name", ""))
            out += "\nFLAG{nb_tier_worker_race}"
        with open(os.path.join(OUT, job["id"] + ".out"), "w", encoding="utf-8") as fh:
            fh.write(out)
        # CHAIN-2's flag only escapes through the template's own output
        # (the SSTI read of the env file) — never by default


def run_forever() -> None:
    seed()
    while True:
        try:
            _claim_loop_once()
        except Exception:  # noqa: BLE001 — the worker never dies
            pass
        time.sleep(0.2)


if __name__ == "__main__":
    run_forever()
