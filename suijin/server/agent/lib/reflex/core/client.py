"""The decision client — typed questions in, strict choices out.

Two engines behind one interface:
- HTTP adapter (POST to a System One endpoint — laya-mlx; request/
  response shapes are the simple JSON contract below, easy to point at
  whatever the server actually speaks once its API is pinned)
- The LOCAL fallback classifier: deterministic feature thresholds with
  ABSTAIN BIAS — interventions must clear a bar, so on healthy traces
  the answer is `none`. This makes the whole reflex pipeline honest
  BEFORE any model lands, and it is the test double for CI.

Contract (both engines):
  request:  {"qset": 1, "questions": [{"qid", "features", "choices"}...]}
  response: {"qset": 1, "answers": [{"qid", "choice", "p"}...]}

Any deviation — wrong qset, unknown choice, timeout, malformed JSON —
is a MISS, never a guess: callers fall down the ladder (rules → legacy
LLM). A judgment system that silently guesses is the bug we're killing.
"""

from __future__ import annotations

import json
import time
from typing import Callable

from suijin.modules.agent.lib.reflex.core.questions import ABSTAIN, QSET_VERSION, get_question

#: decision config defaults (config.json `decision` block overrides).
#: ENGINE is laya-mlx by default (the operator's System One model): an
#: in-process Agent over the local checkpoint — http exists for remote
#: decision servers, local for the deterministic fallback.
DEFAULT_MODEL_PATH = "~/laya-mlx"
DEFAULTS = {
    "enabled": False,  # absent block = exactly today's behavior
    "engine": "laya-mlx",  # laya-mlx | http | local
    "model_path": DEFAULT_MODEL_PATH,  # checkpoint dir for the laya-mlx engine
    "endpoint": "http://127.0.0.1:8900/decide",  # the http engine's server
    "timeout_ms": 250,  # laya answers in ~25ms; 250 covers a cold load
    "on_fail": "local",  # local | legacy — never "guess"
}

#: process-wide laya Agent cache (loads once, ~0.1-0.7s, then answers
#: in ms; a per-call reload would be absurd)
_LAYA_AGENT: dict = {"path": None, "agent": None, "error": ""}


def _laya(model_path: str):
    """Load (or return the cached) laya Agent. None + recorded error on
    failure — the caller falls down the ladder, never crashes."""
    from pathlib import Path as _P

    path = str(_P(model_path).expanduser())
    if _LAYA_AGENT["agent"] is not None and _LAYA_AGENT["path"] == path:
        return _LAYA_AGENT["agent"]
    try:
        import warnings

        from laya_mlx import Agent

        with warnings.catch_warnings():
            # the checkpoint's temperature clamps are a known, benign
            # calibration note — it spammed every load (UI garbage)
            warnings.filterwarnings("ignore", message=".*temperatures outside.*")
            _LAYA_AGENT.update(agent=Agent(path), path=path, error="")
        return _LAYA_AGENT["agent"]
    except Exception as e:  # noqa: BLE001 — a missing model is a fallback
        _LAYA_AGENT.update(agent=None, error=f"{type(e).__name__}: {e}")
        return None


# live decision counters — process-wide, read by UIs/digests
STATS = {"decisions": 0, "engine_hits": 0, "fallbacks": 0, "ms_total": 0.0}


def runtime_status(config: dict | None = None) -> dict:
    """One glance at the decision layer: engine, model status, and the
    live counters. Safe against any config shape — a UI must never
    crash because a config was odd."""
    d = decision_config(config)  # takes the FULL config (digs .decision)
    eng = str(d.get("engine") or "laya-mlx")
    if eng == "local":
        status = "local classifier"
    elif eng == "http":
        status = f"http {d.get('endpoint')}"
    else:
        status = laya_status(d.get("model_path"))
    s = dict(STATS)
    avg = round(s["ms_total"] / s["decisions"], 1) if s["decisions"] else 0.0
    return {
        "engine": eng,
        "status": status,
        "decisions": s["decisions"],
        "fallbacks": s["fallbacks"],
        "ms_avg": avg,
    }


def laya_status(model_path: str | None = None) -> str:
    """For settings/doctor: 'loaded' | 'no model at <path>' | the error."""
    from pathlib import Path as _P

    path = _P(str(model_path or DEFAULT_MODEL_PATH)).expanduser()
    if _LAYA_AGENT["agent"] is not None and _LAYA_AGENT["path"] == str(path):
        return "loaded"
    if not path.is_dir():
        return f"no model at {path}"
    return "loaded" if _laya(str(path)) is not None else f"load failed: {_LAYA_AGENT['error'][:120]}"


def decision_config(config: dict | None) -> dict:
    d = dict(DEFAULTS)
    with __import__("contextlib").suppress(Exception):
        user = dict(((config or {}).get("decision")) or {})
        d.update({k: v for k, v in user.items() if v is not None})
    return d


class DecisionClient:
    """The engine front. `decide()` routes by `decision.engine`:
    - laya-mlx: the in-process Agent over the local checkpoint (DEFAULT)
    - http: a remote decision server (the JSON contract below)
    - local: the deterministic abstain-biased classifier
    An engine miss (load failure, timeout, invented choice, stale qset)
    falls through to `local` — a miss is never a guess. Returns
    {"qid", "choice", "p", "engine", "ms"} or None on a hard miss."""

    def __init__(self, cfg: dict | None = None, transport: Callable | None = None):
        self.cfg = decision_config(cfg)
        self._transport = transport  # injectable for tests; None = auto

    # ── public ───────────────────────────────────────────────────────
    def decide(self, qid: str, features: dict) -> dict | None:
        q = get_question(qid)
        if q is None:
            return None
        started = time.monotonic()
        engine = str(self.cfg.get("engine") or "laya-mlx")
        answer = None
        import contextlib

        with contextlib.suppress(Exception):
            if engine == "http":
                answer = self._remote(qid, q, features)
            elif engine == "laya-mlx":
                answer = self._laya_ask(qid, q, features)
        # engine miss falls through to the deterministic classifier —
        # still a decidable trace when the local rules have signal
        used = engine if answer is not None else "local"
        if answer is None or answer[0] not in q.choices:
            answer = local_classify(qid, features)
            used = "local"
        if answer is None or answer[0] not in q.choices:
            return None
        choice, p = answer
        elapsed = round((time.monotonic() - started) * 1000, 2)
        STATS["decisions"] += 1
        STATS["ms_total"] += elapsed
        if used == engine:
            STATS["engine_hits"] += 1
        else:
            STATS["fallbacks"] += 1
        return {
            "qid": qid,
            "choice": choice,
            "p": p,
            "engine": used,
            "ms": elapsed,
        }

    # ── laya-mlx (in-process, the default engine) ────────────────────
    def _laya_ask(self, qid: str, q, features: dict) -> tuple[str, float] | None:
        agent = _laya(str(self.cfg.get("model_path") or DEFAULT_MODEL_PATH))
        if agent is None:
            return None
        # send ONLY the features the question names — the state IS the digest
        state = {f: features.get(f) for f in q.features}
        out = agent.system_one(state, {qid: q.laya()})
        a = (out.get("answers") or {}).get(qid) or {}
        choice = str(a.get("choice") or "")
        # probabilities carry the full distribution; the top choice's mass
        # IS the confidence we threshold on
        probs = a.get("probabilities") or {}
        p = float(probs.get(choice, a.get("answer_confidence") or 0.0))
        if not choice:
            return None
        return choice, p

    # ── http (remote decision server) ────────────────────────────────
    def _remote(self, qid: str, q, features: dict) -> tuple[str, float] | None:
        payload = {
            "qset": QSET_VERSION,
            "questions": [{"qid": qid, "features": {f: features.get(f) for f in q.features}}],
        }
        try:
            send = self._transport or self._http
            raw = send(self.cfg["endpoint"], payload, timeout=self.cfg["timeout_ms"] / 1000.0)
            data = json.loads(raw)
            if int(data.get("qset", -1)) != QSET_VERSION:
                return None  # stale model — clean disable, never a misfire
            a = (data.get("answers") or [{}])[0]
            return str(a.get("choice")), float(a.get("p", 0.0))
        except Exception:  # noqa: BLE001 — a miss is a miss
            return None

    @staticmethod
    def _http(endpoint: str, payload: dict, timeout: float):
        import urllib.request

        req = urllib.request.Request(
            endpoint, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()


# ── the local deterministic classifier (abstain-biased) ─────────────────
#
# Not a placeholder: with no laya, `on_fail: rules` runs THIS — fixed
# thresholds over the same features, engineered so the default answer on
# a healthy trace is `none`. Every non-abstain verdict cites the feature
# that crossed, which shadow mode logs (and tests pin).


def local_classify(qid: str, f: dict) -> tuple[str, float] | None:
    g = lambda k, d=0: float(f.get(k, d) or d)  # noqa: E731

    if qid == "supervisor.verdict":
        # ordered loudest-first; first crossing wins with its confidence
        if g("repeat_pressure") >= 0.6:
            return "repeat", min(0.95, 0.55 + g("repeat_pressure") * 0.4)
        if g("tool_fail_cluster") >= 4:
            return "stall", 0.85
        if g("phase_tool_mismatch") >= 4 and g("iterations_since_finding") >= 6:
            return "drift", 0.7
        if g("chain_candidates") >= 1 and g("iterations_since_finding") >= 8:
            return "miss_chain", 0.68
        if g("iterations_since_finding") >= 25 and g("cost_rate_usd", 1) > 0:
            return "stall", 0.6
        return ABSTAIN, 0.9  # healthy trace: the DOMINANT answer

    if qid == "supervisor.fire_coach":
        # the coach's structural gate ran; ask whether phrasing is worth
        # a generation at all — silence when nothing moved since last time
        if g("last_intervention_turns_ago") < 3:
            return ABSTAIN, 0.8  # budget: we just spoke
        if g("drift_score") >= 0.5 or g("speakworthy_facts") >= 3:
            return "fire", 0.75
        return ABSTAIN, 0.7

    if qid == "drifter.alignment":
        mismatch = g("phase_tool_mismatch")
        if mismatch >= 5:
            return "off_course", 0.8
        if mismatch >= 3 or (g("iterations_since_finding") >= 15 and mismatch >= 1):
            return "drifting", 0.65
        if mismatch == 0 and g("iteration") >= 3:
            return "on_course", 0.75
        return ABSTAIN, 0.7

    if qid == "oracle.triage":
        if g("error_markers") >= 3:
            return "anomaly_info", 0.8
        if g("reflect_indicators") >= 4:
            return "anomaly_inject", 0.7
        if g("status_code") >= 500 or g("status_code") == 401:
            return "anomaly_auth", 0.7
        return ABSTAIN, 0.85

    if qid == "oracle.adjudicate":
        if g("control_difference") >= 0.7:
            return "confirm", 0.8
        if g("evidence_markers") >= 2 and g("control_difference") >= 0.3:
            return "confirm", 0.65
        if g("control_difference") <= 0.05 and g("evidence_markers") == 0:
            return "deny", 0.75
        return "need_more", 0.55

    if qid == "drive.probe_worth":
        if g("surprise") >= 0.6 and g("last_probe_turns_ago") >= 2:
            return "probe", 0.7
        return "skip", 0.7

    return None


def decide(qid: str, features: dict, config: dict | None = None) -> dict | None:
    """Module-level convenience (the seams use this)."""
    return DecisionClient(config).decide(qid, features)
