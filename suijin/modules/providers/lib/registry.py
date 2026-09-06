"""Provider registry — every OpenAI-compatible provider is a TABLE ROW.

Adding a provider = adding a ProviderSpec. No per-provider code. The
generic compat engine in providers/__init__.py (_compat_call) drives
every spec: streaming, one non-stream fallback, usage recording, diag,
the 401/402/403/429 vocabulary, retries with backoff.

Three families:
  - cloud   : key-gated (activates when its env var appears in .env)
  - local   : no key, localhost base URLs, never priced (the governor
              can never stop on a free local model)
  - custom  : operator-declared boxes at arbitrary IP:port — config.json
              "custom_providers": [{"name", "base_url", "api_key"?, "model"?}]
              addressed as provider "custom:<name>"
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    base_url: str  # OpenAI-compatible base; engine appends /chat/completions
    key_envs: tuple[str, ...] = ()  # any-of; empty = keyless (local)
    default_model: str = ""
    pricing: tuple[float, float] | None = None  # per-M USD (input, output); None = unpriced
    local: bool = False
    note: str = ""
    inline_key: str = ""  # custom: providers may carry their key in config

    @property
    def requires_key(self) -> bool:
        return bool(self.key_envs)


def _spec(key, label, base_url, key_env, default_model, pricing=None, note=""):
    return ProviderSpec(
        key=key,
        label=label,
        base_url=base_url,
        key_envs=(key_env,) if key_env else (),
        default_model=default_model,
        pricing=pricing,
        local=not key_env,
        note=note,
    )


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    # ---- big cloud (OpenAI-compatible endpoints) ----
    "openai": _spec(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
        "gpt-5.1",
        (1.25, 10.0),
    ),
    "xai": _spec(
        "xai",
        "xAI Grok",
        "https://api.x.ai/v1",
        "XAI_API_KEY",
        "grok-4.1-fast",
        (0.20, 0.50),
    ),
    "mistral": _spec(
        "mistral",
        "Mistral AI",
        "https://api.mistral.ai/v1",
        "MISTRAL_API_KEY",
        "mistral-large-latest",
        (2.0, 6.0),
    ),
    "groq": _spec(
        "groq",
        "Groq",
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
        "llama-3.3-70b-versatile",
        (0.59, 0.79),
    ),
    "together": _spec(
        "together",
        "Together AI",
        "https://api.together.xyz/v1",
        "TOGETHER_API_KEY",
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        (0.27, 0.85),
    ),
    "fireworks": _spec(
        "fireworks",
        "Fireworks AI",
        "https://api.fireworks.ai/inference/v1",
        "FIREWORKS_API_KEY",
        "accounts/fireworks/models/llama4-maverick-instruct",
        (0.22, 0.88),
    ),
    "deepinfra": _spec(
        "deepinfra",
        "DeepInfra",
        "https://api.deepinfra.com/v1/openai",
        "DEEPINFRA_API_KEY",
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        (0.27, 0.85),
    ),
    "cerebras": _spec(
        "cerebras",
        "Cerebras",
        "https://api.cerebras.ai/v1",
        "CEREBRAS_API_KEY",
        "llama-3.3-70b",
        (0.85, 1.20),
    ),
    "sambanova": _spec(
        "sambanova",
        "SambaNova",
        "https://api.sambanova.ai/v1",
        "SAMBANOVA_API_KEY",
        "Meta-Llama-4-Maverick-17B-128E-Instruct",
        (0.60, 1.80),
    ),
    "perplexity": _spec(
        "perplexity",
        "Perplexity",
        "https://api.perplexity.ai",
        "PERPLEXITY_API_KEY",
        "sonar-pro",
        (3.0, 15.0),
        note="search-grounded answers",
    ),
    "cohere": _spec(
        "cohere",
        "Cohere",
        "https://api.cohere.ai/compatibility/v1",
        "COHERE_API_KEY",
        "command-a-03-2025",
        (2.50, 10.0),
    ),
    "lambda": _spec(
        "lambda",
        "Lambda Labs",
        "https://api.lambda.ai/v1",
        "LAMBDA_API_KEY",
        "llama4-maverick-instruct",
        (0.25, 0.85),
    ),
    # ---- aggregator: one key → every vendor model ----
    "openrouter": _spec(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        "openrouter/auto",
        None,
        note="one key, every major model; 'openrouter/auto' routes automatically",
    ),
    # ---- Chinese platforms (endpoint-only: activates on key presence) ----
    "zhipu": _spec(
        "zhipu",
        "Zhipu AI (GLM open platform)",
        "https://open.bigmodel.cn/api/paas/v4",
        "ZHIPU_API_KEY",
        "glm-5.2",
        None,
        note="Zhipu GLM open platform (bigmodel.cn) — separate from the zai coding endpoint",
    ),
    "moonshot": _spec(
        "moonshot",
        "Moonshot AI (Kimi)",
        "https://api.moonshot.ai/v1",
        "MOONSHOT_API_KEY",
        "kimi-k2.7-code-highspeed",
        None,
        note="Kimi (international endpoint); CN users: api.moonshot.cn with the same key",
    ),
    "moonshot_cn": _spec(
        "moonshot_cn",
        "Moonshot AI (Kimi, CN)",
        "https://api.moonshot.cn/v1",
        "MOONSHOT_API_KEY",
        "kimi-k2-thinking-turbo",
        None,
        note="Kimi mainland-China endpoint",
    ),
    "qwen": _spec(
        "qwen",
        "Alibaba Qwen (DashScope intl)",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY",
        "qwen3.7-max",
        None,
        note="Qwen via DashScope international (OpenAI compatible-mode)",
    ),
    "qwen_cn": _spec(
        "qwen_cn",
        "Alibaba Qwen (DashScope CN)",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY",
        "qwen3.7-max",
        None,
        note="Qwen via DashScope Beijing (OpenAI compatible-mode)",
    ),
    "volcengine": _spec(
        "volcengine",
        "Volcengine Ark (Doubao)",
        "https://ark.cn-beijing.volces.com/api/v3",
        "VOLCENGINE_API_KEY",
        "doubao-seed-2-0-lite-260428",
        None,
        note="ByteD Doubao via Volcengine Ark",
    ),
    "stepfun": _spec(
        "stepfun",
        "StepFun",
        "https://api.stepfun.com/v1",
        "STEPFUN_API_KEY",
        "step-3.7-flash",
        None,
        note="StepFun (阶跃星辰) flagship models",
    ),
    "modelscope": _spec(
        "modelscope",
        "ModelScope (Alibaba)",
        "https://api-inference.modelscope.cn/v1",
        "MODELSCOPE_API_KEY",
        "ZhipuAI/GLM-4.5",
        None,
        note="ModelScope inference API — free tier for many CN models",
    ),
    "siliconflow": _spec(
        "siliconflow",
        "SiliconFlow",
        "https://api.siliconflow.cn/v1",
        "SILICONFLOW_API_KEY",
        "deepseek-ai/DeepSeek-V4-Flash",
        None,
        note="CN aggregator (siliconflow.cn); intl: api.siliconflow.com",
    ),
    "iflytek": _spec(
        "iflytek",
        "iFlytek (Spark)",
        "https://apis.iflow.cn/v1",
        "IFLYTEK_API_KEY",
        "qwen3-coder-plus",
        None,
        note="iFlytek open platform (OpenAI-compatible gateway)",
    ),
    "sensenova": _spec(
        "sensenova",
        "SenseTime SenseNova",
        "https://token.sensenova.cn/v1",
        "SENSENOVA_API_KEY",
        "sensenova-6.8-flash-lite",
        None,
        note="SenseTime SenseNova token endpoint",
    ),
    "xiaomi": _spec(
        "xiaomi",
        "Xiaomi MiMo",
        "https://api.xiaomimimo.com/v1",
        "XIAOMI_API_KEY",
        "mimo-v2.5-pro-ultraspeed",
        None,
        note="Xiaomi's MiMo model family",
    ),
    "longcat": _spec(
        "longcat",
        "Meituan LongCat",
        "https://api.longcat.chat/openai",
        "LONGCAT_API_KEY",
        "LongCat-2.0",
        None,
        note="Meituan's LongCat (OpenAI-compatible path)",
    ),
    # ---- Western platforms (endpoint-only: activates on key presence) ----
    "nvidia": _spec(
        "nvidia",
        "NVIDIA NIM",
        "https://integrate.api.nvidia.com/v1",
        "NVIDIA_API_KEY",
        "qwen/qwen3-next-80b-a3b-instruct",
        None,
        note="NVIDIA build.nvidia.com inference (1k free credits at signup)",
    ),
    "novita": _spec(
        "novita",
        "Novita AI",
        "https://api.novita.ai/openai",
        "NOVITA_API_KEY",
        "deepseek-ai/DeepSeek-V4-Flash",
        None,
        note="cheap multi-vendor catalog",
    ),
    "nebius": _spec(
        "nebius",
        "Nebius AI Studio",
        "https://api.tokenfactory.nebius.com/v1",
        "NEBIUS_API_KEY",
        "deepseek-ai/DeepSeek-V4-Flash",
        None,
        note="Nebius token-factory endpoint",
    ),
    "baseten": _spec(
        "baseten",
        "Baseten",
        "https://inference.baseten.co/v1",
        "BASETEN_API_KEY",
        "deepseek-ai/DeepSeek-V4-Flash",
        None,
        note="dedicated + serverless model serving",
    ),
    "ollama_cloud": _spec(
        "ollama_cloud",
        "Ollama Cloud",
        "https://ollama.com/v1",
        "OLLAMA_CLOUD_API_KEY",
        "gpt-oss:20b",
        None,
        note="hosted Ollama — the same models as local, no GPU needed",
    ),
    "chutes": _spec(
        "chutes",
        "Chutes",
        "https://llm.chutes.ai/v1",
        "CHUTES_API_KEY",
        "deepseek-ai/DeepSeek-V4-Flash",
        None,
        note="Bittensor-network inference, many free tiers",
    ),
    "poe": _spec(
        "poe",
        "Poe",
        "https://api.poe.com/v1",
        "POE_API_KEY",
        "claude-sonnet-4-6",
        None,
        note="one subscription, every major bot/model",
    ),
    "kilo": _spec(
        "kilo",
        "Kilo AI",
        "https://api.kilo.ai/api/gateway",
        "KILO_API_KEY",
        "qwen/qwen3.7-max",
        None,
        note="multi-vendor gateway with generous free credits",
    ),
    "edenai": _spec(
        "edenai",
        "Eden AI",
        "https://api.edenai.run/v3",
        "EDENAI_API_KEY",
        "deepseek-ai/DeepSeek-V4-Flash",
        None,
        note="aggregator across dozens of providers, one billing",
    ),
    "digitalocean": _spec(
        "digitalocean",
        "DigitalOcean GenAI",
        "https://inference.do-ai.run/v1",
        "DIGITALOCEAN_API_KEY",
        "gpt-4o",
        None,
        note="DO's managed inference platform",
    ),
    "vultr": _spec(
        "vultr",
        "Vultr Serverless Inference",
        "https://api.vultrinference.com/v1",
        "VULTR_API_KEY",
        "deepseek-ai/DeepSeek-V4-Flash",
        None,
        note="Vultr's serverless model catalog",
    ),
    "scaleway": _spec(
        "scaleway",
        "Scaleway Generative APIs",
        "https://api.scaleway.ai/v1",
        "SCALEWAY_API_KEY",
        "gemma-4-26b-a4b-it",
        None,
        note="EU-hosted generative platform",
    ),
    "ovhcloud": _spec(
        "ovhcloud",
        "OVHcloud Endpoints AI",
        "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "OVHCLOUD_API_KEY",
        "qwen3guard-gen-0.6b",
        None,
        note="EU sovereign hosting",
    ),
    "friendli": _spec(
        "friendli",
        "FriendliAI",
        "https://api.friendli.ai/serverless/v1",
        "FRIENDLI_API_KEY",
        "deepseek-ai/DeepSeek-V4-Flash",
        None,
        note="optimized serverless inference",
    ),
    "upstage": _spec(
        "upstage",
        "Upstage Solar",
        "https://api.upstage.ai/v1/solar",
        "UPSTAGE_API_KEY",
        "solar-mini",
        None,
        note="Korean Solar LLM family",
    ),
    "inference_net": _spec(
        "inference_net",
        "Inference.net",
        "https://inference.net/v1",
        "INFERENCE_NET_API_KEY",
        "qwen/qwen-2.5-7b-vision-instruct",
        None,
        note="low-cost multi-vendor inference",
    ),
    "crusoe": _spec(
        "crusoe",
        "Crusoe Cloud",
        "https://api.inference.crusoecloud.com/v1",
        "CRUSOE_API_KEY",
        "deepseek-ai/DeepSeek-V4-Flash",
        None,
        note="climate-aligned AI infrastructure",
    ),
    # ---- local (keyless, never priced) ----
    "ollama": _spec(
        "ollama",
        "Ollama (local)",
        "http://localhost:11434/v1",
        "",
        "qwen3-coder:14b",
        None,
        note="ollama pull <model>; models list at localhost:11434/api/tags",
    ),
    "lmstudio": _spec(
        "lmstudio",
        "LM Studio (local)",
        "http://localhost:1234/v1",
        "",
        "",
        None,
        note="load a model in the app first",
    ),
    "vllm": _spec(
        "vllm",
        "vLLM (local)",
        "http://localhost:8000/v1",
        "",
        "",
        None,
        note="vllm serve <model>",
    ),
    "llamacpp": _spec(
        "llamacpp",
        "llama.cpp server (local)",
        "http://localhost:8080/v1",
        "",
        "",
        None,
        note="llama-server -m <gguf>",
    ),
    "jan": _spec(
        "jan",
        "Jan (local)",
        "http://localhost:1337/v1",
        "",
        "",
        None,
        note="Jan app local server",
    ),
}

#: Chinese-platform keys (display group; endpoint-only until the key appears)
CHINA_KEYS = [
    "zhipu",
    "moonshot",
    "moonshot_cn",
    "qwen",
    "qwen_cn",
    "volcengine",
    "stepfun",
    "modelscope",
    "siliconflow",
    "iflytek",
    "sensenova",
    "xiaomi",
    "longcat",
]

#: Western-platform keys added in the 30-provider expansion (display group)
WESTERN_KEYS = [
    "nvidia",
    "novita",
    "nebius",
    "baseten",
    "ollama_cloud",
    "chutes",
    "poe",
    "kilo",
    "edenai",
    "digitalocean",
    "vultr",
    "scaleway",
    "ovhcloud",
    "friendli",
    "upstage",
    "inference_net",
    "crusoe",
]

#: display order for `suijin providers` / Settings
CLOUD_KEYS = [
    "openai",
    "xai",
    "mistral",
    "groq",
    "together",
    "fireworks",
    "deepinfra",
    "cerebras",
    "sambanova",
    "perplexity",
    "cohere",
    "lambda",
    "openrouter",
]
LOCAL_KEYS = ["ollama", "lmstudio", "vllm", "llamacpp", "jan"]

#: env var shown/tested per provider (first key_env)
KEY_ENV_BY_PROVIDER = {k: (s.key_envs[0] if s.key_envs else "") for k, s in PROVIDER_REGISTRY.items()}


def registry_model_config_key(provider_key: str) -> str:
    """Config key overriding a registry provider's default model ('groq_model')."""
    return f"{provider_key}_model"


def resolve_custom_provider(name: str, config: dict | None) -> ProviderSpec | None:
    """custom:<name> → ProviderSpec from config['custom_providers'].

    Any IP:port box on the LAN running an OpenAI-compatible server:
        "custom_providers": [{"name": "labbox", "base_url": "http://10.0.0.5:8000/v1"}]
    then provider "custom:labbox".
    """
    for entry in (config or {}).get("custom_providers") or []:
        if str(entry.get("name", "")).strip() == name:
            base = str(entry.get("base_url", "")).strip().rstrip("/")
            if not base:
                return None
            return ProviderSpec(
                key=f"custom:{name}",
                label=f"custom · {name}",
                base_url=base,
                key_envs=(),  # key rides the config entry, not the env
                default_model=str(entry.get("model", "") or ""),
                pricing=None,
                local=base.startswith(
                    (
                        "http://localhost",
                        "http://127.",
                        "http://0.0.0.0",
                        "http://192.168.",
                        "http://10.",
                        "http://172.",
                    )
                ),
                note=str(entry.get("note", "") or "operator-declared endpoint"),
                inline_key=str(entry.get("api_key", "") or ""),
            )
    return None
