"""
suijin/core/constants.py — Centralized constants and magic strings.

All model IDs, default ports, scoring thresholds, and provider names
live here. Import from this module instead of hardcoding strings.
"""

from __future__ import annotations

from pathlib import Path

# ── Provider / Model IDs ────────────────────────────────────────────
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_HUGGINGFACE = "huggingface"
PROVIDER_GEMINI = "gemini"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_AMD = "amd"
PROVIDER_ZAI = "zai"

DEFAULT_MODEL = "deepseek-v4-flash"
SENTINEL_MODEL = "Qwen/Qwen2.5-3B-Instruct"
SUPERVISOR_MODEL = "Qwen/Qwen2.5-3B-Instruct"
GEMINI_MODEL = "gemini-2.5-flash"
ZAI_MODEL = "glm-5.3"
# "coding" = GLM Coding Plan subscription endpoint (default — burns plan
# credits, not dollars). "paas" = pay-as-you-go endpoint (per-token USD).
ZAI_ENDPOINT = "coding"

EXPERT_MODELS = [
    "Qwen/Qwen3-Coder-480B-A35B-Instruct",
    "zai-org/GLM-5.1",
    "deepseek-ai/DeepSeek-V4-Pro",
    "deepseek-ai/DeepSeek-V4-Flash",
]

# ── Default Ports ───────────────────────────────────────────────────
BLUE_LAB_PORT = 5906
PROXY_DEFAULT_PORT = 41730  # obscure high port (operator request)
METASPLOIT_RPC_PORT = 55553

# ── Scoring Thresholds ──────────────────────────────────────────────
SCORE_CRITICAL = 8
SCORE_SUSPICIOUS = 5
SCORE_DECEIVE = 6
SCORE_BLOCK = 8
SCORE_SHADOW = 9
PATTERN_SCORE_THRESHOLD = 5
BASELINE_REQUESTS = 25
RISK_HIGH = 7

# ── Deception ───────────────────────────────────────────────────────
TARPIT_MAX_DELAY = 15.0
TARPIT_WINDOW_MINUTES = 30
TARPIT_DEFAULT_DELAY = 5.0

# ── Timeouts ────────────────────────────────────────────────────────
LLM_TIMEOUT = 45
TOOL_TIMEOUT = 60
BATCH_TIMEOUT = 95
HTTP_TIMEOUT = 20
FIREWALL_TIMEOUT = 5
PROXY_FORWARD_TIMEOUT = 30

# ── Limits ──────────────────────────────────────────────────────────
MAX_ITERATIONS = 100
MAX_SUBAGENTS = 50
MAX_WATCHERS_PER_ENDPOINT = 3
MAX_RECENT_REQUESTS = 50
TRUNCATE_LIMIT = 50000

# ── File paths (base tmp dir configurable via SUIJIN_TMP_DIR) ───────
import os

TMP_DIR = Path(os.environ.get("SUIJIN_TMP_DIR") or os.environ.get("MEDUSA_TMP_DIR") or "/tmp")

BLUE_KG_PATH = TMP_DIR / "blue_kg.json"
BLUE_TRAFFIC_LOG = TMP_DIR / "blue_defend_traffic.jsonl"
BLUE_TARPIT_FILE = TMP_DIR / "blue_tarpit.json"
BLUE_HONEYPOT_FILE = TMP_DIR / "blue_honeypots.json"
