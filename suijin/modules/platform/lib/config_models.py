"""Pydantic config models — validates config.json and blue_config.json at startup.

Catches typos, missing required fields, and type mismatches before they
cause silent runtime failures.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, field_validator


class ScorerConfig(BaseModel):
    signal_weights: dict[str, int] = Field(default_factory=dict)
    sql_keywords_weight: int = Field(default=4, ge=0, le=10)
    xss_pattern_weight: int = Field(default=3, ge=0, le=10)
    path_traversal_weight: int = Field(default=3, ge=0, le=10)
    unknown_param_weight: int = Field(default=2, ge=0, le=10)
    new_ip_weight: int = Field(default=2, ge=0, le=10)
    unusual_method_weight: int = Field(default=2, ge=0, le=10)
    body_size_anomaly_weight: int = Field(default=1, ge=0, le=10)
    burst_penalty: int = Field(default=1, ge=0, le=10)
    critical_threshold: int = Field(default=8, ge=1, le=10)
    suspicious_threshold: int = Field(default=5, ge=1, le=10)


class WatcherConfig(BaseModel):
    max_per_endpoint: int = Field(default=3, ge=1, le=20)
    spawn_on_threshold: int = Field(default=20, ge=1)
    health_check_interval: int = Field(default=30, ge=5)
    context_rotation_requests: int = Field(default=200, ge=10)


class DeceptionConfig(BaseModel):
    auto_honeypot: bool = True
    auto_tarpit: bool = True
    tarpit_delay_seconds: float = Field(default=8.0, ge=0.5, le=30.0)
    max_tarpit_requests: int = Field(default=100, ge=1)
    canary_tokens: bool = True
    shadow_redirect_threshold: int = Field(default=8, ge=1, le=10)


class ResponseConfig(BaseModel):
    auto_block_critical: bool = True
    auto_block_suspicious: bool = False
    max_blocks_per_hour: int = Field(default=50, ge=1)
    unblock_after_hours: int = Field(default=24, ge=1)


class HotfixConfig(BaseModel):
    auto_patch_critical: bool = False
    patch_timeout_minutes: int = Field(default=5, ge=1, le=60)
    test_before_deploy: bool = True
    silent_patch_mode: bool = True


class SOCConfig(BaseModel):
    tier1_per_endpoint: bool = True
    tier2_count: int = Field(default=3, ge=1, le=20)
    threat_hunter_count: int = Field(default=1, ge=0, le=5)
    shift_check_interval: int = Field(default=60, ge=10)


class CostConfig(BaseModel):
    daily_budget_usd: float = Field(default=5.0, ge=0.0)
    alert_threshold_usd: float = Field(default=3.0, ge=0.0)
    max_llm_calls_per_minute: int = Field(default=20, ge=1)


class BlueConfig(BaseModel):
    """Validates blue_config.json at load time."""

    traffic_normalization_turns: int = Field(default=25, ge=5, le=500)
    scorer: ScorerConfig = Field(default_factory=ScorerConfig)
    watchers: WatcherConfig = Field(default_factory=WatcherConfig)
    deception: DeceptionConfig = Field(default_factory=DeceptionConfig)
    response: ResponseConfig = Field(default_factory=ResponseConfig)
    hotfix: HotfixConfig = Field(default_factory=HotfixConfig)
    soc: SOCConfig = Field(default_factory=SOCConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    provider: str = Field(default="deepseek")


class CostCapWarning(UserWarning):
    """Configured cost cap is high — advisory only. Silenced by default at
    the entrypoints (the raw UserWarning echoed pydantic internals as a
    wall of text on every boot); the engagement path shows ONE red line."""


class RedConfig(BaseModel):
    """Validates config.json at load time."""

    provider: str = Field(default="deepseek")
    deepseek_model: str = Field(default="deepseek-v4-flash")
    zai_model: str = Field(default="glm-5.3")
    zai_endpoint: str = Field(default="coding")
    max_iterations: int = Field(default=100, ge=1, le=10000)
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    supervisor_interval: int = Field(default=5, ge=1)
    cost_hard_cap_usd: float = Field(default=0.0, ge=0.0)  # 0 = unlimited (operator)
    cost_budget_usd: float = Field(default=0.0, ge=0.0)  # 0 = unlimited (operator)
    cost_alert_usd: float = Field(default=0.0, ge=0.0)  # 0 = disabled (operator)
    expert_models: List[str] = Field(default_factory=list)
    final_model_id: str = ""
    sentinel_model_id: str = ""
    max_tokens_per_request: int = Field(default=8000, ge=100)
    stealth: bool = Field(default=True)  # v5.1: quiet by default (masked identity, pacing, tool rate caps)

    @field_validator("zai_endpoint")
    @classmethod
    def validate_zai_endpoint(cls, v):
        allowed = ("coding", "paas")
        v = (v or "coding").strip().lower()
        if v not in allowed and not v.startswith(("http://", "https://")):
            raise ValueError(
                f"zai_endpoint must be one of {allowed} (or a full base URL), got '{v}'. "
                "'coding' = GLM Coding Plan subscription quota (default); "
                "'paas' = pay-as-you-go per-token billing."
            )
        return v

    @field_validator("cost_hard_cap_usd")
    @classmethod
    def warn_high_cap(cls, v):
        if v > 50.0:
            import warnings

            warnings.warn(f"Cost cap ${v:.2f} is high.", CostCapWarning, stacklevel=2)
        return v
