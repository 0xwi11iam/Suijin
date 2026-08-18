from __future__ import annotations

import os
import time

import requests as req
from huggingface_hub import InferenceClient
from rich.console import Console

console = Console()

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
    "priced": False,  # False if any model was missing from MODEL_PRICING
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
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # tolerate provider prefixes / suffixes (e.g. "anthropic/claude-opus-4-8")
    for key, price in MODEL_PRICING.items():
        if key in model:
            return price
    return None


def _record_usage(provider, model, in_tok, out_tok):
    """Add one call's token usage to the running tally. Never raises."""
    try:
        in_tok = int(in_tok or 0)
        out_tok = int(out_tok or 0)
        USAGE["calls"] += 1
        USAGE["input_tokens"] += in_tok
        USAGE["output_tokens"] += out_tok
        price = _price_for(model)
        if price is not None:
            USAGE["priced"] = True  # at least one exact price was used
        else:
            price = DEFAULT_RATE  # estimate anyway so the guardrail works
        in_rate, out_rate = price
        USAGE["est_cost_usd"] += (in_tok * in_rate + out_tok * out_rate) / 1_000_000
    except Exception:
        # Cost accounting must never break an actual model call.
        pass


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
            "priced": False,
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
    console.print(
        f"[yellow]Unknown zai_endpoint '{setting}' — using '{ZAI_DEFAULT_ENDPOINT}' "
        f"({ZAI_ENDPOINTS[ZAI_DEFAULT_ENDPOINT]}). Valid: coding, paas, or a full URL.[/yellow]"
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


def _lobstertrap_available():
    """Check if LobsterTrap proxy is running."""
    try:
        resp = req.get(LOBSTERTRAP_DASHBOARD, timeout=1)
        return resp.status_code == 200
    except Exception:
        return False


def _call_via_lobstertrap(messages, model, temperature, max_tokens):
    """
    Send request through LobsterTrap proxy using OpenAI-compatible API.
    LobsterTrap inspects and enforces policy before forwarding.
    """
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        resp = req.post(
            f"{LOBSTERTRAP_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        elif resp.status_code == 403:
            return f"Error: LobsterTrap DENIED — {resp.json().get('message', 'Policy violation')}"
        else:
            return None
    except Exception:
        return None


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
):
    if config is None:
        from medusa.core.red.config_loader import load_config

        config = load_config()

    provider = config.get("provider", "deepseek").lower()
    temp = temperature if temperature is not None else config.get("temperature", 0.4)
    mtokens = max_tokens if max_tokens is not None else config.get("max_tokens_per_request", 8000)

    # ---------- LobsterTrap proxy check ----------
    if _lobstertrap_available():
        console.print("[bold green][LobsterTrap] Active — inspecting prompt...[/bold green]")
        from medusa.core.red.config_loader import active_model

        lt_model = model_id or active_model(config)
        lt_result = _call_via_lobstertrap(messages, lt_model, temp, mtokens)
        if lt_result is not None:
            if lt_result.startswith("Error: LobsterTrap DENIED"):
                console.print(f"[bold red]{lt_result}[/bold red]")
                return lt_result
            return lt_result
        else:
            console.print("[yellow][LobsterTrap] Proxy failed — falling back to direct provider[/yellow]")

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
                    if um is not None:
                        _record_usage(
                            "gemini",
                            model_name,
                            getattr(um, "prompt_token_count", 0),
                            getattr(um, "candidates_token_count", 0),
                        )
                except Exception:
                    pass
                return response.text
            except Exception as e:
                err_str = str(e).lower()
                if "quota" in err_str or "429" in err_str:
                    console.print("[bold red]Gemini quota exhausted.[/bold red]")
                elif "api_key" in err_str or "invalid" in err_str:
                    return "Error: Invalid Gemini API Key"
                console.print(f"[yellow]Gemini attempt {attempt + 1} failed: {e}[/yellow]")
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
                    if u is not None:
                        _record_usage(
                            "huggingface", hf_model, getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0)
                        )
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
                    if u is not None:
                        # count cache reads/writes as input tokens for cost
                        in_tok = (
                            getattr(u, "input_tokens", 0)
                            + getattr(u, "cache_creation_input_tokens", 0)
                            + getattr(u, "cache_read_input_tokens", 0)
                        )
                        _record_usage("anthropic", model_name, in_tok, getattr(u, "output_tokens", 0))
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
                    console.print("[bold red]Anthropic rate-limited or overloaded.[/bold red]")
                console.print(f"[yellow]Anthropic attempt {attempt + 1} failed: {e}[/yellow]")
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
                resp = req.post(
                    f"{endpoint}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=120,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        u = data.get("usage") or {}
                        _record_usage("amd", amd_model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    except Exception:
                        pass
                    return data["choices"][0]["message"]["content"]
                elif resp.status_code == 402:
                    return "Error: 402"
                else:
                    console.print(f"[yellow]AMD error {resp.status_code}: {resp.text[:200]}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]AMD request failed: {e}[/yellow]")
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
        for attempt in range(retries):
            try:
                resp = req.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=45,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        u = data.get("usage") or {}
                        _record_usage("deepseek", ds_model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    except Exception:
                        pass
                    msg = data["choices"][0]["message"]
                    return msg.get("content") or msg.get("reasoning_content", "") or "(empty response)"
                elif resp.status_code == 401:
                    return "Error: Invalid DeepSeek API Key"
                elif resp.status_code == 402:
                    return "Error: 402"
                elif resp.status_code == 429:
                    console.print("[bold red]DeepSeek rate-limited.[/bold red]")
                else:
                    console.print(f"[yellow]DeepSeek error {resp.status_code}: {resp.text[:200]}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]DeepSeek attempt {attempt + 1} failed: {e}[/yellow]")
            if attempt < retries - 1:
                time.sleep(2 * (2**attempt))  # 2s, 4s, 8s backoff
        return "Error: DeepSeek API Timeout"

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
        for attempt in range(retries):
            try:
                resp = req.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=45,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        u = data.get("usage") or {}
                        _record_usage("zai", zai_model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    except Exception:
                        pass
                    msg = data["choices"][0]["message"]
                    return msg.get("content") or msg.get("reasoning_content", "") or "(empty response)"
                elif resp.status_code == 401:
                    return "Error: Invalid Z.ai API Key"
                elif resp.status_code == 402:
                    return "Error: 402 (insufficient Z.ai credits)"
                elif resp.status_code == 403:
                    # Classic cause: Coding Plan key hitting the pay-as-you-go
                    # endpoint (or vice versa). Point at the fix instead of
                    # retrying blindly — 403s don't heal with retries.
                    return (
                        "Error: Z.ai 403 — this key can't use the selected endpoint. "
                        f'Coding Plan: set zai_endpoint="coding" ({ZAI_CODING_BASE_URL}). '
                        f'Pay-as-you-go: set zai_endpoint="paas" ({ZAI_PAAS_BASE_URL}). '
                        "Adjust in Settings, or check your plan at z.ai/manage-apikey."
                    )
                elif resp.status_code == 429:
                    console.print(
                        "[bold red]Z.ai rate-limited (plan credits may be exhausted — "
                        "5h/weekly quotas reset automatically).[/bold red]"
                    )
                else:
                    console.print(f"[yellow]Z.ai error {resp.status_code}: {resp.text[:200]}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]Z.ai attempt {attempt + 1} failed: {e}[/yellow]")
            if attempt < retries - 1:
                time.sleep(2 * (2**attempt))  # 2s, 4s, 8s backoff
        return "Error: Z.ai API Timeout"

    return f"Error: Unknown provider '{provider}'"


def generate_with_failover(messages, config=None, **kwargs) -> str:
    """generate() across a provider fallback chain (config['fallback_providers']).

    The chain is [<primary from config.provider>, *config.fallback_providers].
    Falls through ONLY on hard failures (Error:/timeout strings) — successful
    outputs (including KB-disabled guidance from tools) pass straight back.
    """
    cfg = dict(config or {})
    chain = [cfg.get("provider", "deepseek")]
    chain += [p for p in (cfg.get("fallback_providers") or []) if p != chain[0]]
    last = ""
    for provider in chain:
        cfg["provider"] = str(provider).lower()
        out = generate(messages, cfg, **kwargs)
        if not str(out).startswith("Error:"):
            return out
        last = out
    return last
