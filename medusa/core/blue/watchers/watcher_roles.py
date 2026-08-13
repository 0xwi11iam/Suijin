"""Watcher role definitions for SOC team simulation."""
from __future__ import annotations
TIER1_ANALYST = {"name": "Tier 1 Analyst", "level": 1, "autonomy": "rate_limit_only",
    "escalates_to": "tier2", "tools": ["classify_attack", "rate_limit", "log"]}
TIER2_ANALYST = {"name": "Tier 2 Analyst", "level": 2, "autonomy": "deception_and_block",
    "escalates_to": "soc_lead", "tools": ["validate_threat", "deploy_honeypot", "shadow_redirect", "block_ip"]}
SOC_LEAD = {"name": "SOC Lead", "level": 3, "autonomy": "full_strategic",
    "escalates_to": "operator", "tools": ["declare_incident", "campaign_analysis", "resource_allocation"]}
THREAT_HUNTER = {"name": "Threat Hunter", "level": 2, "autonomy": "proactive_scanning",
    "escalates_to": "soc_lead", "tools": ["scan_logs", "probe_endpoints", "attack_simulation"]}
INCIDENT_COMMANDER = {"name": "Incident Commander", "level": 3, "autonomy": "incident_management",
    "escalates_to": "operator", "tools": ["forensic_collection", "hotfix_trigger", "operator_notify"]}
