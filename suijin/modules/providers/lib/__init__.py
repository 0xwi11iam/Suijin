from __future__ import annotations

import contextlib
import os
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
}

# Rough public list prices in USD per 1,000,000 tokens (input, output).
# These are estimates for the cost guardrail — NOT billing-grade. Unknown
# models contribute 0.0 to est_cost_usd and flip USAGE["priced"] to a
# best-effort flag so the UI can label the number as approximate.
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
    # Z.ai GLM (coding-plan models; requests for older GLM ids are
    # auto-routed by the server, e.g. glm-5.1 -> glm-5.3).
    "glm-5.3": (0.80, 2.60),
    "glm-5.3-flash": (0.14, 0.70),
    "glm-5-turbo": (0.50, 2.00),
    "glm-5.1": (0.60, 2.20),
    "glm-5.1-flash": (0.11, 0.58),
    "glm-4.7": (0.60, 2.20),
    "glm-4.7-flash": (0.11, 0.58),
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


def _price_for(model):
    """Return (input_$per_1M, output_$per_1M) for a model id, or None."""
    if not model:
        return None
    m = str(model).strip()
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
        USAGE["estimated_calls" if estimated else "api_reported_calls"] += 1
        price = _price_for(model)
        if price is not None:
            USAGE["priced"] = USAGE["priced"] and True  # exact price used
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
    try:
        with _HTTP.post(url, headers=headers, json=p, timeout=_TIMEOUT, stream=True) as resp:
            if resp.status_code != 200:
                return resp.status_code, "", "", None, (resp.text or "")[:400]
            _first_token_deadline = time.monotonic() + 60.0  # provider sends NOTHING in 60s → kill
            for line in resp.iter_lines(decode_unicode=True):
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
            return 200, "".join(content), "".join(reasoning), usage, ""
    except Exception as e:
        logger.debug(f"stream transport error: {e}")
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
        # model intelligence (operator's Ctrl+Space tier, applied between
        # thoughts): glm-4.5+ accepts a thinking toggle; low disables it
        intel = str((config or {}).get("intelligence", "") or "").lower()
        if intel in ("max", "high", "medium", "low"):
            payload["thinking"] = {"type": "disabled" if intel == "low" else "enabled"}
            if intel == "medium":
                payload["max_tokens"] = max(1000, int(mtokens) // 2)
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
