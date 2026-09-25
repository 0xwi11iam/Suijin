from __future__ import annotations

import contextlib
import os
import threading
import time

import requests as req
from huggingface_hub import InferenceClient
from rich.console import Console

console = Console()
import logging  # noqa: E402 — provider retry noise goes to logs, not the engagement console

logger = logging.getLogger("suijin.providers")

# ----------------------------------------------------------------------
# HTTP transport (shared) — TLS smoothing + honest timeouts
# ----------------------------------------------------------------------
# One Session for every OpenAI-compatible call: the TLS handshake and TCP
# setup happen ONCE, then keep-alive carries the connection between
# iterations (a fresh handshake per call was a large slice of the
# perceived 10-20s dead time before first output).
_HTTP = req.Session()
# (connect, read): 10s to establish, 120s for the body. The 300s read
# was a 5-minute invisible stall per LLM call (the operator saw "it
# stopped"); 120s covers the biggest legitimate completions, and the
# retry loop handles the rest.
_TIMEOUT = (10, 120)

#: Stream inactivity bounds (2026-09-12): no line for 90s or 10 min total
#: → the watchdog closes the response (see _stream_chat — the measured
#: half-dead-socket hang requests' own timeout never fired on).
_STREAM_IDLE_S = 90.0
_STREAM_TOTAL_S = 600.0

# ----------------------------------------------------------------------
# Token / cost accounting
# ----------------------------------------------------------------------
# Every provider returns token-usage metadata, but historically we threw it
# away. The supervisor's cost guardrail needs a running tally, so we keep a
# module-level accumulator here. This is deliberately NON-invasive:
# generate() still returns a plain string, so no existing caller breaks.
# Read the tally with get_usage(); zero it at the start of a run with
# reset_usage().
USAGE = {
    "calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "est_cost_usd": 0.0,
    "priced": True,  # False once any call uses DEFAULT_RATE (label as approximate)
    # v5.1 accuracy accounting:
    "api_reported_calls": 0,  # calls whose tokens came from the API response
    "estimated_calls": 0,  # calls whose tokens were client-side estimated
    "by_provider": {},  # provider -> {calls, input, output, cost_usd}
    # the real input size of the most recent request (the ctx gauge's truth)
    "last_request_input_tokens": 0,
}

#: the effort tier vocabulary, descending; callers gate by model support
_EFFORT_LADDER = ("max", "xhigh", "high", "med", "low")
_EFFORT_ALIASES = {"medium": "med", "minimum": "low", "maximum": "max"}


def _resolve_effort_tier(model: str, requested: str, provider: str = "") -> str:
    """Map the requested tier to the model's vocabulary. When models.dev
    knows the model's effort levels: exact/alias/nearest-neighbour. When
    it doesn't (generic endpoints, custom providers): pass the alias-
    resolved tier through unchanged — the endpoint rejects what it
    doesn't understand and the caller's default stays intact."""
    want = _EFFORT_ALIASES.get(requested, requested)
    if want not in _EFFORT_LADDER:
        return ""
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib.model_meta import supported_effort_levels

        supported = [str(v).lower() for v in supported_effort_levels(provider or "zai", str(model or ""))]
        if not supported:
            return want  # unknown model: trust the operator's tier name
        if want in supported:
            return want
        idx = _EFFORT_LADDER.index(want)
        for offset in range(1, len(_EFFORT_LADDER)):
            for cand in (idx - offset, idx + offset):
                if 0 <= cand < len(_EFFORT_LADDER) and _EFFORT_LADDER[cand] in supported:
                    return _EFFORT_LADDER[cand]
    return want


def _apply_effort(
    payload: dict, model: str, config: dict | None, mtokens: int, openai_style: bool = False, provider: str = ""
) -> None:
    """Apply the operator's effort tier to ANY provider's payload.

    Two dialects (2026-09-17):
      zai-style (thinking toggle): low = thinking disabled; graduated
        output budgets for the rest — the proven glm shape.
      openai_style (reasoning_effort field): the OpenAI/OpenRouter
        convention — the tier name emitted directly.
    When models.dev knows the model, the tier snaps to its supported
    vocabulary; when it doesn't, the alias-resolved name passes through
    (generic endpoints). Never raises."""
    with contextlib.suppress(Exception):
        intel = str((config or {}).get("intelligence", "") or "").lower()
        if not intel:
            return
        tier = _resolve_effort_tier(str(model or ""), intel, provider=provider)
        if not tier:
            return
        if openai_style:
            payload["reasoning_effort"] = tier
        else:
            payload["thinking"] = {"type": "disabled" if tier == "low" else "enabled"}
            budget_frac = {"low": 0.0, "med": 0.5, "high": 0.75, "xhigh": 0.9, "max": 1.0}.get(tier, 1.0)
            if budget_frac < 1.0:
                payload["max_tokens"] = max(1000, int(int(mtokens) * budget_frac))


# Rough public list prices in USD per 1,000,000 tokens (input, output).
# These are estimates for the cost guardrail — NOT billing-grade. Unknown
# models contribute 0.0 to est_cost_usd and flip USAGE["priced"] to a
# best-effort flag so the UI can label the number as approximate.
#: OFFLINE FALLBACK ONLY — the live source of truth is models.dev
#: (resolve_pricing, cached 7d). This table answers when the catalog is
#: unreachable; such costs are flagged approximate (USAGE["priced"]=False).
MODEL_PRICING = {
    # Anthropic
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # Google Gemini
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    # Cheap sentinel/supervisor tier (HF-hosted small models ~ negligible)
    "Qwen/Qwen2.5-3B-Instruct": (0.05, 0.10),
    "Qwen/Qwen2.5-7B-Instruct": (0.20, 0.30),
    "Qwen/Qwen2.5-72B-Instruct": (0.60, 0.90),
    "meta-llama/Llama-3.1-8B-Instruct": (0.10, 0.20),
    "mistralai/Mistral-7B-Instruct-v0.3": (0.10, 0.20),
    "deepseek-ai/DeepSeek-V3": (0.30, 0.90),
    "Qwen/Qwen3-Coder-480B-A35B-Instruct": (0.20, 0.60),
    "zai-org/GLM-5.1": (0.60, 2.20),
    "deepseek-v4-flash": (0.27, 1.10),
    "deepseek-v4-pro": (0.55, 2.19),
    # Z.ai GLM PAYG rates (models.dev, verified 2026-09-23 — the old
    # table ran glm-5.3 at 0.80/2.60 and under-billed ~70%). Coding-plan
    # requests bill CREDITS, not USD: usage carries the plan flag and the
    # cost line is labeled a PAYG-equivalent estimate.
    "glm-5.3": (1.40, 4.40),
    "glm-5.3-flash": (0.15, 0.50),
    "glm-5.3-flashx": (0.37, 1.25),
    "glm-5.3-highspeed": (1.40, 4.40),  # plan-only id; PAYG equivalent
    "glm-5-turbo": (0.50, 2.00),
    "glm-5.1": (1.40, 4.40),
    "glm-5.1-flash": (0.15, 0.50),
    "glm-4.7": (0.60, 2.20),
    "glm-4.7-flash": (0.11, 0.58),
    "glm-4.7-flashx": (0.07, 0.40),
    "glm-4.6": (0.60, 2.20),
    # Legacy
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}

# Fallback rate ($/1M in, out) used when a model isn't in MODEL_PRICING, so the
# cost guardrail still gets a (rough) dollar estimate instead of $0.00. A run
# using a fallback rate is flagged with USAGE["priced"] = False so the UI can
# show the number as approximate.
DEFAULT_RATE = (0.20, 0.60)


def _price_for(model, provider: str = ""):
    """(input_$per_1M, output_$per_1M) for a model id — LIVE-FIRST:

    1. operator config (custom_providers, their word for their endpoint)
    2. the models.dev catalog (cached 7d — the source of truth; coding-
       plan rows price at 0 and resolve to the PAYG equivalent)
    3. MODEL_PRICING + registry specs — the OFFLINE fallback only,
       labeled approximate via USAGE["priced"] = False
    """
    if not model:
        return None
    m = str(model).strip()
    # 1+2. live resolution (config first, then the catalog)
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.services import get as _service

        _cfg = _service("red_config") or {}
        from suijin.modules.providers.lib.model_meta import resolve_pricing

        _pair = resolve_pricing(str(provider or _cfg.get("provider") or ""), m, _cfg)
        if _pair:
            USAGE["_priced_exact"] = True  # live rate, not a fallback
            return _pair
    # 3. offline fallbacks (approximate)
    USAGE["_priced_exact"] = False
    if m in MODEL_PRICING:
        return MODEL_PRICING[m]
    # registry providers price their own default models
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY

        for _spec in PROVIDER_REGISTRY.values():
            if _spec.default_model and _spec.default_model.split("/")[-1].lower() in m.lower() and _spec.pricing:
                return _spec.pricing
    # tolerate provider prefixes / suffixes (e.g. "anthropic/claude-opus-4-8")
    # and case drift ("deepseek-ai/DeepSeek-V4-Flash" vs "deepseek-v4-flash")
    m_lower = m.lower()
    for key, price in MODEL_PRICING.items():
        if key.lower() in m_lower:
            return price
    return None


def estimate_tokens(text) -> int:
    """Client-side token ESTIMATE for when an API omits usage.

    Word+punctuation-aware (a real approximation, not chars/4):
    Germanic text averages ~1.3 tokens/word for BPE vocabularies;
    JSON/code punctuation splits into per-symbol tokens. CJK counts
    roughly one token per character.
    """
    if not text:
        return 0
    if not isinstance(text, str):
        try:
            import json as _json

            text = _json.dumps(text)
        except Exception:  # noqa: BLE001
            text = str(text)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff")
    rest = "".join(ch for ch in text if not ("\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff"))
    words = len(rest.split())
    punct = sum(1 for ch in rest if not ch.isalnum() and not ch.isspace())
    return int(cjk + words * 1.3 + punct * 0.5)


def _flag_plan_billing():
    """True when the ACTIVE zai endpoint is the Coding Plan — those calls
    burn plan credits, not USD. The cost tally then carries
    plan_billing=True so displays can label dollars as PAYG-equivalent."""
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.services import get as _service

        _cfg = _service("red_config") or {}
        _prov = str(_cfg.get("provider") or "")
        if _prov == "zai" and str(_cfg.get("zai_endpoint") or "coding").strip().lower() == "coding":
            USAGE["plan_billing"] = True


def _record_usage(provider, model, in_tok, out_tok, estimated: bool = False):
    """Add one call's token usage to the running tally. Never raises.

    estimated=True marks tokens counted client-side (the API omitted
    usage) — surfaced by get_usage()/`suijin tokens` so accuracy is
    never silently overstated."""
    try:
        in_tok = int(in_tok or 0)
        out_tok = int(out_tok or 0)
        USAGE["calls"] += 1
        USAGE["input_tokens"] += in_tok
        USAGE["output_tokens"] += out_tok
        # THE LIVE CTX GAUGE'S TRUTH (2026-09-17): the real input size of
        # the request JUST sent — the red-teamer reads this instead of
        # diffing cumulative counters (parallel background calls polluted
        # the diff). Per-request, written at record time, race-tight.
        USAGE["last_request_input_tokens"] = in_tok
        USAGE["estimated_calls" if estimated else "api_reported_calls"] += 1
        if provider == "zai":
            _flag_plan_billing()
        price = _price_for(model, provider=str(provider or ""))
        if price is not None:
            # exact = live models.dev rate or operator config; the offline
            # table is honest about being approximate
            USAGE["priced"] = USAGE["priced"] and bool(USAGE.pop("_priced_exact", False))
        else:
            USAGE["priced"] = False  # fallback rate in play — cost is approximate
            price = DEFAULT_RATE  # estimate anyway so the guardrail works
        in_rate, out_rate = price
        cost = (in_tok * in_rate + out_tok * out_rate) / 1_000_000
        USAGE["est_cost_usd"] += cost
        slot = USAGE["by_provider"].setdefault(str(provider), {"calls": 0, "input": 0, "output": 0, "cost_usd": 0.0})
        slot["calls"] += 1
        slot["input"] += in_tok
        slot["output"] += out_tok
        slot["cost_usd"] += cost
    except Exception:
        # Cost accounting must never break an actual model call.
        pass


def record_missing_usage(messages, response_text, provider, model) -> None:
    """Called when an API response omitted usage: client-side estimate,
    clearly flagged. Previously such calls recorded ZERO tokens — a
    silent undercount that made the governor under-stop."""
    est_in = estimate_tokens(" ".join(str(m.get("content", "")) for m in (messages or [])))
    est_out = estimate_tokens(response_text)
    _record_usage(provider, model, est_in, est_out, estimated=True)


def get_usage():
    """Return a copy of the running token/cost tally."""
    return dict(USAGE)


def reset_usage():
    """Zero the tally — call at the start of each operation."""
    USAGE.update(
        {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "est_cost_usd": 0.0,
            "plan_billing": False,
            "priced": True,
            "api_reported_calls": 0,
            "estimated_calls": 0,
            "by_provider": {},
        }
    )


# Gemini is optional – only imported when actually needed
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

# Z.ai serves two separate chat-completions APIs that accept the same key
# but bill completely differently. The user picks via config["zai_endpoint"]:
#   "coding" (DEFAULT) — GLM Coding Plan subscription endpoint. Burns plan
#     credits (Lite/Pro/Max quotas), never pay-as-you-go dollars. Supported
#     models: glm-5.3, glm-5-turbo, glm-4.7 (older GLM ids auto-route to
#     glm-5.3 server-side).
#   "paas"  — pay-as-you-go endpoint. Per-token USD billing; full GLM model
#     catalogue. Choose this if you don't have a Coding Plan subscription,
#     otherwise calls will 403 (subscription quota can't be used there).
# https://docs.z.ai/devpack/tool/others
ZAI_CODING_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
ZAI_PAAS_BASE_URL = "https://api.z.ai/api/paas/v4"
ZAI_ENDPOINTS = {"coding": ZAI_CODING_BASE_URL, "paas": ZAI_PAAS_BASE_URL}
ZAI_DEFAULT_ENDPOINT = "coding"


def _zai_base_url(config):
    """Resolve the Z.ai base URL from config. Defaults to the Coding Plan.

    Accepts either a plan name ("coding" / "paas") or a full custom base URL
    (useful for proxies). Unknown values fall back to the Coding Plan with a
    warning instead of silently hitting the wrong billing surface.
    """
    setting = (config.get("zai_endpoint") or "").strip().lower() if config else ""
    if not setting:
        return ZAI_ENDPOINTS[ZAI_DEFAULT_ENDPOINT]
    if setting in ZAI_ENDPOINTS:
        return ZAI_ENDPOINTS[setting]
    if setting.startswith(("http://", "https://")):
        return setting.rstrip("/")
    logger.warning(
        f"Unknown zai_endpoint '{setting}' — using '{ZAI_DEFAULT_ENDPOINT}' "
        f"({ZAI_ENDPOINTS[ZAI_DEFAULT_ENDPOINT]}). Valid: coding, paas, or a full URL."
    )
    return ZAI_ENDPOINTS[ZAI_DEFAULT_ENDPOINT]


# Anthropic is optional – only imported when actually needed
try:
    import anthropic
except ImportError:
    anthropic = None

# ----------------------------------------------------------------------
# LobsterTrap proxy integration
# ----------------------------------------------------------------------
LOBSTERTRAP_URL = "http://localhost:8080/v1"
LOBSTERTRAP_DASHBOARD = "http://localhost:8080/_lobstertrap/"


# ----------------------------------------------------------------------
# Gemini setup
# ----------------------------------------------------------------------
def _init_gemini(config):
    if genai is None:
        raise RuntimeError("google-genai is not installed. Run: pip install google-genai")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("Gemini provider selected but no api_key set. Use Settings to add your key.")
    client = genai.Client(api_key=api_key)
    return client


# ----------------------------------------------------------------------
# Anthropic setup
# ----------------------------------------------------------------------
def _init_anthropic(config):
    if anthropic is None:
        raise RuntimeError("anthropic is not installed. Run: pip install anthropic")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "Anthropic provider selected but no api_key set. Use Settings to add your key, or export ANTHROPIC_API_KEY."
        )
    return anthropic.Anthropic(api_key=api_key)


# ----------------------------------------------------------------------
# OpenAI-compatible transport: streaming + non-streaming, shared session
# ----------------------------------------------------------------------


def _emit(on_delta, kind, piece):
    if on_delta is not None and piece:
        if not _diag_ttft["seen"]:
            _diag_first_token()
        with contextlib.suppress(Exception):  # display must never break generation
            on_delta(kind, piece)


_diag_ttft = {"t0": None, "seen": False}


def _diag_llm_start(provider: str, model: str, msgs: int):
    from suijin.kernel.diag import diag

    _diag_ttft["t0"] = time.monotonic()
    _diag_ttft["seen"] = False
    diag("llm_start", provider=provider, model=model, msgs=msgs)


def _diag_llm_done(provider: str, model: str, ok: bool, wall_s: float):
    from suijin.kernel.diag import diag

    ttft = round(time.monotonic() - _diag_ttft["t0"], 2) if _diag_ttft["t0"] else None
    diag("llm_done", provider=provider, model=model, ok=ok, wall_s=round(wall_s, 2), ttft_s=ttft)


def _diag_first_token():
    _diag_ttft["seen"] = True


def _stream_chat(url, headers, payload, on_delta=None):
    """Stream an OpenAI-compatible chat completion (SSE).

    Returns (status, content, reasoning, usage, body):
      status 200 — content/reasoning assembled, usage from the final chunk
        (stream_options.include_usage; None when the gateway omits it)
      status != 200 — non-2xx HTTP: body carries a short error excerpt
      status 0 — transport/stream failure BEFORE completion; whatever
        landed is returned but the caller should fall back to non-stream
    """
    import json as _json

    p = dict(payload)
    p["stream"] = True
    p["stream_options"] = {"include_usage": True}
    content: list[str] = []
    reasoning: list[str] = []
    usage = None
    # INACTIVITY WATCHDOG (2026-09-12, measured): a half-dead socket left
    # iter_lines blocked in SSL_read FOREVER — requests' read timeout
    # (10,120) does not fire inside urllib3's buffered stream reads, the
    # stalled call sat in the MAIN thread, and it swallowed the process
    # SIGTERM too (the run hung 30+ min past its watchdog). A daemon
    # thread CLOSES the response when no line arrives for _STREAM_IDLE_S
    # (any line — tokens or SSE keep-alives — proves liveness), which
    # forces the blocked read to raise; the caller's existing status-0
    # path then does its ONE non-stream fallback and the run recovers.
    _idle = {"last": time.monotonic(), "killed": False}

    def _stream_watchdog(resp_ref, idle_ref, idle_s, total_s, started):
        while not idle_ref["killed"]:
            time.sleep(2.0)
            now = time.monotonic()
            if now - started > total_s or now - idle_ref["last"] > idle_s:
                idle_ref["killed"] = True
                with contextlib.suppress(Exception):
                    resp_ref.close()
                return

    try:
        with _HTTP.post(url, headers=headers, json=p, timeout=_TIMEOUT, stream=True) as resp:
            if resp.status_code != 200:
                return resp.status_code, "", "", None, (resp.text or "")[:400]
            _wt = threading.Thread(
                target=_stream_watchdog,
                args=(resp, _idle, _STREAM_IDLE_S, _STREAM_TOTAL_S, time.monotonic()),
                daemon=True,
            )
            _wt.start()
            _first_token_deadline = time.monotonic() + 60.0  # provider sends NOTHING in 60s → kill
            for line in resp.iter_lines(decode_unicode=True):
                _idle["last"] = time.monotonic()
                if time.monotonic() > _first_token_deadline and not content and not reasoning:
                    return 0, "", "", None, "first-token timeout: provider sent no data in 60s"
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = _json.loads(data)
                except Exception:
                    continue
                u = obj.get("usage")
                if isinstance(u, dict) and u:
                    usage = u  # the include_usage chunk carries the totals
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        content.append(piece)
                        _emit(on_delta, "content", piece)
                    rpiece = delta.get("reasoning_content")
                    if rpiece:
                        reasoning.append(rpiece)
                        _emit(on_delta, "reasoning", rpiece)
            if _idle["killed"]:
                return 0, "".join(content), "".join(reasoning), usage, "stream idle: closed by watchdog"
            return 200, "".join(content), "".join(reasoning), usage, ""
    except Exception as e:
        logger.debug(f"stream transport error: {e}")
        if _idle["killed"]:
            return 0, "".join(content), "".join(reasoning), usage, "stream idle: closed by watchdog"
        return 0, "".join(content), "".join(reasoning), usage, str(e)[:200]


def _post_chat(url, headers, payload):
    """Non-streaming OpenAI-compatible call on the shared session.
    Returns (status_code, parsed_json_or_None, body_text)."""
    resp = _HTTP.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
    body = resp.text or ""
    try:
        return resp.status_code, resp.json(), body
    except Exception:
        return resp.status_code, None, body


def _diagnose_transport(exc) -> str:
    """Actionable transport diagnosis — TLS vs DNS vs connect vs read.
    The operator sees the CAUSE, not a raw traceback (field runs kept
    hitting 'SSL'/'read timed out' blobs with zero guidance)."""
    name = type(exc).__name__
    low = str(exc).lower()
    if "ssl" in low or "tls" in low or "certificate" in low or name == "SSLError":
        return (
            f"TLS handshake failed — endpoint refused/broke the secure handshake (VPN, proxy, or cert issue) [{name}]"
        )
    if "connect" in low and "timeout" in low:
        return f"connect timeout — host did not answer in 10s (network down, firewall, or wrong endpoint) [{name}]"
    if "read timed out" in low or name == "ReadTimeout":
        return f"read timeout — no bytes within the read window (provider stalled) [{name}]"
    if "getaddrinfo" in low or "nodename" in low or "name or service" in low:
        return f"DNS failure — the API hostname did not resolve [{name}]"
    if "connection" in low:
        return f"connection failed — reset/refused mid-transport (often VPN/proxy flapping) [{name}]"
    return f"{name}: {str(exc)[:120]}"


# ----------------------------------------------------------------------
# Model catalog — where a provider's model ids come from
# ----------------------------------------------------------------------
def provider_models_endpoint(provider, config=None):
    """(base_url, headers) for listing a provider's model ids, or
    (None, reason) when the layer can't know it.

    Endpoints live HERE (this layer owns provider wiring); callers like the
    Settings TUI ask instead of keeping their own copy. Never raises.

      - registry providers: their spec's base_url + env key
      - custom:<name>: the operator's base_url + key from config
      - zai: the exported coding/paas constants (endpoint per config)
      - amd: its configured endpoint
      - anthropic / gemini / huggingface: SDK-managed — the caller is told
        honestly rather than sent to a guessed URL
    """
    provider = str(provider or "").strip()
    config = dict(config or {})
    if not provider:
        return None, "no provider selected"
    # NOTE: no load_env() here — a resolver must not mutate the process
    # environment. The runner (and the Settings editor) load .env once at
    # boot; everything below reads the environment it finds.
    # --- registry ---
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY

        spec = PROVIDER_REGISTRY.get(provider)
        if spec is not None:
            key = ""
            for env in spec.key_envs:
                key = os.environ.get(env, "") or ""
                if key:
                    break
            if spec.local:
                return None, f"{provider} is a local box — its models are whatever the server loaded"
            return str(spec.base_url or ""), ({"Authorization": f"Bearer {key}"} if key else {})
    # --- custom boxes ---
    if provider.startswith("custom:"):
        want = provider[len("custom:") :]
        for entry in config.get("custom_providers") or []:
            if str(entry.get("name") or "") == want:
                base = str(entry.get("base_url") or "").rstrip("/")
                key = str(entry.get("api_key") or "")
                if not base:
                    return None, f"custom:{want} has no base_url"
                return base, ({"Authorization": f"Bearer {key}"} if key else {})
        return None, f"no custom provider named {want} in config.json"
    # --- bespoke code paths ---
    if provider == "zai":
        base = (
            ZAI_PAAS_BASE_URL if str(config.get("zai_endpoint") or "coding").lower() == "paas" else ZAI_CODING_BASE_URL
        )
        key = os.environ.get("ZAI_API_KEY", "") or ""
        return base, ({"Authorization": f"Bearer {key}"} if key else {})
    if provider == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY", "") or ""
        return "https://api.deepseek.com/v1", ({"Authorization": f"Bearer {key}"} if key else {})
    if provider == "amd":
        base = str((config.get("amd_config") or {}).get("endpoint") or "https://api.amd.com/v1").rstrip("/")
        key = os.environ.get("AMD_API_KEY", "") or ""
        return base, ({"Authorization": f"Bearer {key}"} if key else {})
    return None, (f"{provider} talks through its own SDK — the layer does not expose a model list; type the model id")


def provider_key_env(provider) -> str:
    """The env var that carries THIS provider's API key — the name the
    provider layer itself reads, resolved dynamically.

    Registry providers answer from their spec (one declaration, used
    everywhere). The bespoke code-path providers have hand-written env
    lookups above, so their names are named here beside them. Nothing is
    hardcoded in the UI: a new registry provider needs no entry.
    """
    provider = str(provider or "").strip()
    if not provider:
        return ""
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY

        spec = PROVIDER_REGISTRY.get(provider)
        if spec is not None:
            return str(spec.key_envs[0]) if spec.key_envs else ""
    return {
        "zai": "ZAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "huggingface": "HF_TOKEN",
        "amd": "AMD_API_KEY",
    }.get(provider, "")


def get_provider_key(provider) -> str:
    """The provider's key, read live from the environment (never from
    config.json — a key is a secret, and the config is snapshotted into
    bundles). Empty when unset.

    Deliberately does NOT call load_env(): a read must not mutate the
    process environment. Callers that need the .env on disk loaded (the
    runner at boot, the Settings editor when it opens) call it once."""
    env_name = provider_key_env(provider)
    if not env_name:
        return ""
    return str(os.environ.get(env_name, "") or "")


def set_provider_key(provider, value: str) -> tuple[bool, str]:
    """Write the key into the .env file and the live environment.

    Returns (ok, message). The value never touches config.json and is
    never printed back. Refuses silently-named providers: a key with no
    env var to live in would be written nowhere.
    """
    env_name = provider_key_env(provider)
    if not env_name:
        return False, f"{provider} has no API key (local/custom — nothing to store)"
    value = str(value or "").strip()
    # A pasted key often carries the shell quoting that wrapped it in a
    # terminal or a docs page. load_env() strips a matching pair of quotes
    # when it reads, so normalize here too — otherwise what sits in .env is
    # not what the provider will actually be handed, and the mismatch only
    # shows up later as an inexplicable 401.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not value:
        return False, "no key given"
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import ENV_PATH

        lines = []
        replaced = False
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.split("=", 1)[0].strip() == env_name:
                    lines.append(f"{env_name}={value}")
                    replaced = True
                else:
                    lines.append(line)
        if not replaced:
            lines.append(f"{env_name}={value}")
        try:
            ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
            ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with contextlib.suppress(OSError):
                ENV_PATH.chmod(0o600)  # a secret file stays a secret file
        except OSError as e:
            return False, f"cannot write {ENV_PATH.name} ({e.strerror or e}) — export {env_name} by hand"
    os.environ[env_name] = value  # the live process sees it now
    return True, f"{env_name} saved to .env"


# ----------------------------------------------------------------------
# Core call – all providers
# ----------------------------------------------------------------------
def generate(
    messages,
    config=None,
    *,
    model_id=None,
    temperature=None,
    max_tokens=None,
    retries=3,
    on_delta=None,
):
    """Generate a completion. `on_delta(kind, text)` (kind: "reasoning" |
    "content") receives tokens as they stream, when the provider supports
    it (zai/deepseek) — rendering stays live instead of waiting for the
    entire response. Callback errors are swallowed: display must never
    break generation."""
    if config is None or not str(config.get("provider") or "").strip():
        # None OR PARTIAL config: a truthy dict without "provider" (e.g.
        # {"intelligence": "max"} threaded from the stream wrapper) used to
        # fall through to the deepseek default — silently spending the
        # wrong provider while the operator's config said otherwise.
        from suijin.modules.tools.lib.services import get as _service

        _base = _service("red_config") or {}
        _merged = dict(_base)
        _merged.update(config or {})
        config = _merged

    provider = config.get("provider", "deepseek").lower()
    temp = temperature if temperature is not None else config.get("temperature", 0.4)
    mtokens = max_tokens if max_tokens is not None else config.get("max_tokens_per_request", 8000)

    # ---------- Gemini ----------
    if provider == "gemini":
        client = _init_gemini(config)
        model_name = config.get("gemini_model", "gemini-2.5-flash")

        system_parts = []
        conversation = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                text = content
                if system_parts:
                    text = "[System]\n" + "\n".join(system_parts) + "\n\n" + text
                    system_parts.clear()
                conversation.append(types.Content(role="user", parts=[types.Part(text=text)]))
            elif role == "assistant":
                conversation.append(types.Content(role="model", parts=[types.Part(text=content)]))

        if system_parts:
            if conversation and conversation[-1].role == "user":
                existing = conversation[-1].parts[0].text
                conversation[-1] = types.Content(
                    role="user", parts=[types.Part(text="[System]\n" + "\n".join(system_parts) + "\n\n" + existing)]
                )
            else:
                conversation.append(
                    types.Content(role="user", parts=[types.Part(text="[System]\n" + "\n".join(system_parts))])
                )

        for attempt in range(retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=conversation,
                    config=types.GenerateContentConfig(
                        temperature=temp,
                        max_output_tokens=mtokens,
                    ),
                )
                try:
                    um = getattr(response, "usage_metadata", None)
                    if um is not None and getattr(um, "prompt_token_count", None) is not None:
                        _record_usage(
                            "gemini",
                            model_name,
                            getattr(um, "prompt_token_count", 0),
                            getattr(um, "candidates_token_count", 0),
                        )
                    else:
                        record_missing_usage(messages, response.text, "gemini", model_name)
                except Exception:
                    pass
                return response.text
            except Exception as e:
                err_str = str(e).lower()
                if "quota" in err_str or "429" in err_str:
                    logger.warning("Gemini quota exhausted")
                elif "api_key" in err_str or "invalid" in err_str:
                    return "Error: Invalid Gemini API Key"
                logger.warning(f"Gemini attempt {attempt + 1} failed: {e}")
                time.sleep(5 * (2**attempt))
        return "Error: Gemini API Timeout"

    # ---------- HuggingFace ----------
    if provider == "huggingface":
        token = os.environ.get("HF_TOKEN")
        hf_model = model_id or config.get("final_model_id")
        for attempt in range(retries):
            try:
                client = InferenceClient(model=hf_model, token=token)
                response = client.chat_completion(
                    messages=messages,
                    max_tokens=mtokens,
                    temperature=temp,
                )
                try:
                    u = getattr(response, "usage", None)
                    if u is not None and getattr(u, "prompt_tokens", None) is not None:
                        _record_usage(
                            "huggingface", hf_model, getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0)
                        )
                    else:
                        msg0 = response.choices[0].message
                        record_missing_usage(messages, msg0.content or "", "huggingface", hf_model)
                except Exception:
                    pass
                msg = response.choices[0].message
                reasoning = getattr(msg, "reasoning", None)
                if reasoning:
                    return f"\u4dc2\n{reasoning}\n\u4dc2\n" + (msg.content or "")
                return msg.content or ""
            except Exception as e:
                err_str = str(e).lower()
                if "402" in err_str or "payment required" in err_str:
                    return "Error: 402"
                time.sleep(5 * (2**attempt))
        return "Error: HF API Timeout"

    # ---------- Anthropic ----------
    if provider == "anthropic":
        client = _init_anthropic(config)
        raw_model = model_id or config.get("anthropic_model", "claude-opus-4-7")
        # Anthropic API only accepts claude-* models; remap external model IDs
        if not raw_model.lower().startswith("claude-"):
            raw_model = config.get("anthropic_model", "claude-opus-4-7")
        model_name = raw_model

        system_parts = []
        conversation = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                system_parts.append(content)
            elif role in ("user", "assistant"):
                conversation.append({"role": role, "content": content})

        # Cache the system prompt — meaningful cost reduction on repeated
        # agent calls that share the same large directives block.
        system_param = None
        if system_parts:
            system_param = [
                {
                    "type": "text",
                    "text": "\n\n".join(system_parts),
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        # Anthropic requires the first message in the conversation to be
        # from the user. Synthesise a minimal user turn if needed.
        if not conversation or conversation[0]["role"] != "user":
            conversation.insert(0, {"role": "user", "content": "Begin."})

        request_kwargs = {
            "model": model_name,
            "messages": conversation,
            "max_tokens": mtokens,
            "temperature": temp,
        }
        if system_param is not None:
            request_kwargs["system"] = system_param

        for attempt in range(retries):
            try:
                response = client.messages.create(**request_kwargs)
                try:
                    u = getattr(response, "usage", None)
                    if u is not None and getattr(u, "input_tokens", None) is not None:
                        # count cache reads/writes as input tokens for cost
                        in_tok = (
                            getattr(u, "input_tokens", 0)
                            + getattr(u, "cache_creation_input_tokens", 0)
                            + getattr(u, "cache_read_input_tokens", 0)
                        )
                        _record_usage("anthropic", model_name, in_tok, getattr(u, "output_tokens", 0))
                    else:
                        text_parts0 = [b.text for b in response.content if getattr(b, "type", None) == "text"]
                        record_missing_usage(messages, "".join(text_parts0), "anthropic", model_name)
                except Exception:
                    pass
                text_parts = []
                for block in response.content:
                    if getattr(block, "type", None) == "text":
                        text_parts.append(block.text)
                return "".join(text_parts)
            except Exception as e:
                err_str = str(e).lower()
                if "401" in err_str or "authentication" in err_str or "invalid_api_key" in err_str:
                    return "Error: Invalid Anthropic API Key"
                if "402" in err_str or "credit" in err_str or "billing" in err_str:
                    return "Error: 402"
                if "rate" in err_str or "429" in err_str or "overloaded" in err_str:
                    logger.warning("Anthropic rate-limited or overloaded")
                logger.warning(f"Anthropic attempt {attempt + 1} failed: {e}")
                time.sleep(5 * (2**attempt))
        return "Error: Anthropic API Timeout"

    # ---------- AMD ----------
    if provider == "amd":
        api_key = os.environ.get("AMD_API_KEY")
        endpoint = config.get("amd_config", {}).get("endpoint", "https://api.amd.com/v1")
        amd_model = model_id or config.get("amd_model") or config.get("final_model_id")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": amd_model,
            "messages": messages,
            "max_tokens": mtokens,
            "temperature": temp,
        }
        _apply_effort(payload, amd_model, config, mtokens, provider="amd")
        for attempt in range(retries):
            try:
                resp = _HTTP.post(
                    f"{endpoint}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=_TIMEOUT,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    try:
                        u = data.get("usage") or {}
                        if u.get("prompt_tokens") is None:
                            # gateway omitted usage — estimate, never zero-count
                            record_missing_usage(messages, content, "amd", amd_model)
                        else:
                            _record_usage("amd", amd_model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    except Exception:
                        pass
                    return content
                elif resp.status_code == 402:
                    return "Error: 402"
                else:
                    logger.warning(f"AMD error {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"AMD request failed: {e}")
            time.sleep(5 * (2**attempt))
        return "Error: AMD API Timeout"

    # ---------- DeepSeek ----------
    if provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            return "Error: DeepSeek API key not set. Use Settings to add your key, or export DEEPSEEK_API_KEY."
        # DeepSeek API only accepts deepseek-v4-pro, deepseek-v4-flash.
        raw_model = model_id or config.get("deepseek_model", "deepseek-v4-flash")
        if not raw_model.lower().startswith("deepseek"):
            raw_model = config.get("deepseek_model", "deepseek-v4-flash")
        ds_model = raw_model
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": ds_model,
            "messages": messages,
            "max_tokens": mtokens,
            "temperature": temp,
        }
        _apply_effort(payload, ds_model, config, mtokens, provider="deepseek")
        ds_url = "https://api.deepseek.com/v1/chat/completions"
        _diag_llm_start("deepseek", ds_model, len(messages))
        _ds_t0 = time.monotonic()
        last_diag = "transport failure"
        for attempt in range(retries):
            try:
                status, content, reasoning, usage, body = _stream_chat(ds_url, headers, payload, on_delta=on_delta)
                if status == 0:
                    # stream died in transit — ONE non-stream fallback on the
                    # shared session, then the normal backoff path if that fails
                    code, data, _b = _post_chat(ds_url, headers, payload)
                    if code == 200 and data:
                        msg = data["choices"][0]["message"]
                        content = msg.get("content") or msg.get("reasoning_content", "") or "(empty response)"
                        u = data.get("usage") or {}
                        try:
                            if u.get("prompt_tokens") is None:
                                record_missing_usage(messages, content, "deepseek", ds_model)
                            else:
                                _record_usage(
                                    "deepseek", ds_model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
                                )
                        except Exception:
                            pass
                        return content
                if status == 200:
                    text = content or reasoning or "(empty response)"
                    try:
                        if usage is None or usage.get("prompt_tokens") is None:
                            record_missing_usage(messages, text, "deepseek", ds_model)
                        else:
                            _record_usage(
                                "deepseek", ds_model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
                            )
                    except Exception:
                        pass
                    return text
                elif status == 401:
                    _diag_llm_done("deepseek", ds_model, False, time.monotonic() - _ds_t0)
                    return "Error: Invalid DeepSeek API Key"
                elif status == 402:
                    _diag_llm_done("deepseek", ds_model, False, time.monotonic() - _ds_t0)
                    return "Error: 402"
                elif status == 429:
                    logger.warning("DeepSeek rate-limited")
                elif status != 0:
                    logger.warning(f"DeepSeek error {status}: {body[:200]}")
                last_diag = body[:120] or "stream failed without detail"
            except Exception as e:
                last_diag = _diagnose_transport(e)
            logger.warning(f"DeepSeek attempt {attempt + 1} failed — {last_diag}")
            if attempt < retries - 1:
                time.sleep(2 * (2**attempt))  # 2s, 4s, 8s backoff
        _diag_llm_done("deepseek", ds_model, False, time.monotonic() - _ds_t0)
        return f"Error: DeepSeek API unreachable after {retries} attempt(s) — {last_diag}"

    # ---------- Z.ai (GLM) ----------
    if provider == "zai":
        api_key = os.environ.get("ZAI_API_KEY", "")
        if not api_key:
            return "Error: Z.ai API key not set. Use Settings to add your key, or export ZAI_API_KEY."
        # Z.ai serves glm-* model ids; HF-style ids like "zai-org/GLM-5.3" map to "glm-5.3".
        raw_model = model_id or config.get("zai_model", "glm-5.3")
        if "/" in raw_model:
            raw_model = raw_model.rsplit("/", 1)[-1].lower()
        zai_model = raw_model if raw_model.lower().startswith("glm") else config.get("zai_model", "glm-5.3")
        base_url = _zai_base_url(config)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": zai_model,
            "messages": messages,
            "max_tokens": mtokens,
            "temperature": temp,
        }
        # MODEL EFFORT: the shared applier (zai thinking-toggle dialect)
        _apply_effort(payload, zai_model, config, mtokens, provider="zai")
        zai_url = f"{base_url}/chat/completions"
        _diag_llm_start("zai", zai_model, len(messages))
        _zai_t0 = time.monotonic()
        last_diag = "transport failure"
        for attempt in range(retries):
            try:
                status, content, reasoning, usage, body = _stream_chat(zai_url, headers, payload, on_delta=on_delta)
                if status == 0:
                    # stream died in transit — ONE non-stream fallback on the
                    # shared session, then the normal backoff path if that fails
                    code, data, _b = _post_chat(zai_url, headers, payload)
                    if code == 200 and data:
                        msg = data["choices"][0]["message"]
                        content = msg.get("content") or msg.get("reasoning_content", "") or "(empty response)"
                        u = data.get("usage") or {}
                        try:
                            if u.get("prompt_tokens") is None:
                                record_missing_usage(messages, content, "zai", zai_model)
                            else:
                                _record_usage(
                                    "zai", zai_model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
                                )
                        except Exception:
                            pass
                        return content
                if status == 200:
                    text = content or reasoning or "(empty response)"
                    _diag_llm_done("zai", zai_model, True, time.monotonic() - _zai_t0)
                    try:
                        if usage is None or usage.get("prompt_tokens") is None:
                            # gateway omitted usage — estimate, never zero-count
                            record_missing_usage(messages, text, "zai", zai_model)
                        else:
                            _record_usage(
                                "zai", zai_model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
                            )
                    except Exception:
                        pass
                    return text
                elif status == 401:
                    _diag_llm_done("zai", zai_model, False, time.monotonic() - _zai_t0)
                    return "Error: Invalid Z.ai API Key"
                elif status == 402:
                    _diag_llm_done("zai", zai_model, False, time.monotonic() - _zai_t0)
                    return (
                        "Error: 402 (Z.ai plan quota exhausted or out of credits — "
                        "coding-plan quotas reset every ~5h; pay-as-you-go needs a top-up)"
                    )
                elif status == 403:
                    # Classic cause: Coding Plan key hitting the pay-as-you-go
                    # endpoint (or vice versa). Point at the fix instead of
                    # retrying blindly — 403s don't heal with retries.
                    _diag_llm_done("zai", zai_model, False, time.monotonic() - _zai_t0)
                    return (
                        "Error: Z.ai 403 — this key can't use the selected endpoint. "
                        f'Coding Plan: set zai_endpoint="coding" ({ZAI_CODING_BASE_URL}). '
                        f'Pay-as-you-go: set zai_endpoint="paas" ({ZAI_PAAS_BASE_URL}). '
                        "Adjust in Settings, or check your plan at z.ai/manage-apikey."
                    )
                elif status == 429:
                    logger.warning(
                        "Z.ai rate-limited (plan credits may be exhausted — 5h/weekly quotas reset automatically)"
                    )
                elif status != 0:
                    logger.warning(f"Z.ai error {status}: {body[:200]}")
                last_diag = body[:120] or "stream failed without detail"
            except Exception as e:
                last_diag = _diagnose_transport(e)
            logger.warning(f"Z.ai attempt {attempt + 1} failed — {last_diag}")
            if attempt < retries - 1:
                time.sleep(2 * (2**attempt))  # 2s, 4s, 8s backoff
        _diag_llm_done("zai", zai_model, False, time.monotonic() - _zai_t0)
        return f"Error: Z.ai API unreachable after {retries} attempt(s) — {last_diag}"

    # ---------- Registry providers (OpenAI-compatible table rows) ----------
    from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY, ProviderSpec, resolve_custom_provider

    spec = PROVIDER_REGISTRY.get(provider)
    if spec is None and provider.startswith("custom:"):
        spec = resolve_custom_provider(provider.split(":", 1)[1], config)
    if isinstance(spec, ProviderSpec):
        return _compat_call(
            spec,
            messages,
            config,
            temperature=temp,
            max_tokens=mtokens,
            retries=retries,
            on_delta=on_delta,
            model_id=model_id,
        )

    return f"Error: Unknown provider '{provider}'"


def _compat_call(spec, messages, config, *, temperature, max_tokens, retries, on_delta, model_id=None):
    """The generic OpenAI-compatible engine — one code path for every
    registry provider (cloud table rows AND custom: LAN boxes). Streaming
    first, ONE non-stream fallback on transport death, usage recorded
    (local = unpriced so the cost governor can never stop on free models),
    errors as 'Error:' strings per the failover protocol."""
    import os as _os

    cfg = config or {}
    api_key = ""
    for env in spec.key_envs:
        if _os.environ.get(env, "").strip():
            api_key = _os.environ[env].strip()
            break
    if not api_key:
        api_key = spec.inline_key
    if spec.requires_key and not api_key:
        _env = " or ".join(spec.key_envs)
        return f"Error: {spec.label} API key not set. Add {_env} to .env (suijin env) or Settings."

    model = model_id or cfg.get(f"{spec.key}_model") or spec.default_model
    if not model:
        return (
            f"Error: no model set for {spec.label}. Set '{spec.key}_model' in config.json "
            "(suijin config / Settings) — e.g. the model id your endpoint serves."
        )

    headers = {"Content-Type": "application/json"}
    if api_key:
        # the key is ANY string (no format policing); HTTP headers are
        # latin-1 — non-latin-1 keys fly percent-encoded rather than
        # crashing the transport with UnicodeEncodeError
        try:
            api_key.encode("latin-1")
        except UnicodeEncodeError:
            from urllib.parse import quote

            api_key = quote(api_key, safe="")
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    _apply_effort(payload, model, config, max_tokens, openai_style=True)
    url = f"{spec.base_url.rstrip('/')}/chat/completions"
    _diag_llm_start(spec.key, model, len(messages))
    _t0 = time.monotonic()
    last_diag = "transport failure"
    for attempt in range(retries):
        try:
            status, content, reasoning, usage, body = _stream_chat(url, headers, payload, on_delta=on_delta)
            if status == 0:
                code, data, _b = _post_chat(url, headers, payload)
                if code == 200 and data:
                    msg = data["choices"][0]["message"]
                    content = msg.get("content") or msg.get("reasoning_content", "") or "(empty response)"
                    u = data.get("usage") or {}
                    with contextlib.suppress(Exception):
                        if u.get("prompt_tokens") is None:
                            record_missing_usage(messages, content, spec.key, model)
                        else:
                            _record_usage(spec.key, model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    return content
            if status == 200:
                text = content or reasoning or "(empty response)"
                _diag_llm_done(spec.key, model, True, time.monotonic() - _t0)
                with contextlib.suppress(Exception):
                    if usage is None or usage.get("prompt_tokens") is None:
                        record_missing_usage(messages, text, spec.key, model)
                    else:
                        _record_usage(spec.key, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
                return text
            if status == 401:
                _diag_llm_done(spec.key, model, False, time.monotonic() - _t0)
                return f"Error: Invalid {spec.label} API Key"
            if status == 402:
                _diag_llm_done(spec.key, model, False, time.monotonic() - _t0)
                return f"Error: 402 ({spec.label} rejected the call — insufficient credits/quota)"
            if status == 403:
                _diag_llm_done(spec.key, model, False, time.monotonic() - _t0)
                return (
                    f"Error: {spec.label} 403 — key lacks access to '{model}' (or base_url is wrong "
                    f"for this key). Check the model id and endpoint."
                )
            if status == 404:
                last_diag = f"model '{model}' not found at {spec.base_url}"
            elif status == 429:
                last_diag = "rate-limited (429)"
            elif status != 0:
                last_diag = str(body)[:150]
        except Exception as e:  # noqa: BLE001 — transport errors become strings
            last_diag = _diagnose_transport(e)
        logger.warning(f"{spec.label} attempt {attempt + 1} failed — {last_diag}")
        if attempt < retries - 1:
            time.sleep(2 * (2**attempt))
    _diag_llm_done(spec.key, model, False, time.monotonic() - _t0)
    return f"Error: {spec.label} unreachable after {retries} attempt(s) — {last_diag}"


# Failover telemetry (D29): chain outcomes per process lifetime. Doctor
# surfaces this; tests reset it. Never affects call behavior.
FAILOVER_STATS = {
    "chains": 0,  # generate_with_failover invocations
    "failovers": 0,  # times the primary failed and a fallback answered
    "all_down": 0,  # every provider in the chain failed
    "primary_ok": 0,  # primary answered first try
    "errors_by_provider": {},  # provider -> failure count
    "last_event": "",  # human note for doctor
}


def _failover_event(note: str) -> None:
    FAILOVER_STATS["last_event"] = note


def generate_with_failover(messages, config=None, **kwargs) -> str:
    """generate() across a provider fallback chain (config['fallback_providers']).

    The chain is [<primary from config.provider>, *config.fallback_providers].
    Falls through ONLY on hard failures (Error:/timeout strings) — successful
    outputs (including KB-disabled guidance from tools) pass straight back.
    """
    cfg = dict(config or {})
    chain = [cfg.get("provider", "deepseek")]
    chain += [p for p in (cfg.get("fallback_providers") or []) if p != chain[0]]
    FAILOVER_STATS["chains"] += 1
    last = ""
    for i, provider in enumerate(chain):
        cfg["provider"] = str(provider).lower()
        out = generate(messages, cfg, **kwargs)
        if not str(out).startswith("Error:"):
            if i == 0:
                FAILOVER_STATS["primary_ok"] += 1
                _failover_event(f"{provider} answered (primary)")
            else:
                FAILOVER_STATS["failovers"] += 1
                _failover_event(f"{provider} answered via FAILOVER (primary {chain[0]} failed)")
            return out
        FAILOVER_STATS["errors_by_provider"][cfg["provider"]] = (
            FAILOVER_STATS["errors_by_provider"].get(cfg["provider"], 0) + 1
        )
        last = out
    FAILOVER_STATS["all_down"] += 1
    _failover_event(f"ALL {len(chain)} provider(s) failed: {', '.join(chain)}")
    return last
