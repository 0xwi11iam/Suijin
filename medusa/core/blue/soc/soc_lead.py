"""SOC Lead — strategic decisions, attack campaign analysis, subagent coordination."""
from __future__ import annotations
import asyncio, time


class SOCLead:
    """Coordinates the SOC team. Receives threat intelligence and dispatches responses."""

    def __init__(self):
        self.campaigns: dict = {}       # IP -> campaign tracker
        self.escalations: list = []     # Escalated incidents
        self.decisions: list = []       # Log of decisions made

    async def analyze_campaign(self, attacker_ip: str, attacks: list) -> dict:
        """Correlate multiple attacks from same IP into a campaign assessment."""
        campaign = self.campaigns.get(attacker_ip, {
            "started": time.time(), "attack_count": 0,
            "endpoints": set(), "techniques": set(), "max_score": 0,
        })
        for atk in attacks:
            campaign["attack_count"] += 1
            campaign["endpoints"].add(atk.get("path", "/"))
            campaign["techniques"].add(atk.get("type", "unknown"))
            campaign["max_score"] = max(campaign["max_score"], atk.get("score", 1))
        campaign["sophistication"] = (
            "advanced" if len(campaign["techniques"]) >= 3
            else "intermediate" if len(campaign["techniques"]) >= 2
            else "script_kiddie"
        )
        self.campaigns[attacker_ip] = campaign
        return campaign

    def escalate(self, ip: str, reason: str, score: int):
        self.escalations.append({"ip": ip, "reason": reason, "score": score, "time": time.time()})

    def get_status(self) -> dict:
        return {
            "active_campaigns": len(self.campaigns),
            "escalations": len(self.escalations),
            "decisions": len(self.decisions),
            "top_threats": sorted(
                [{"ip": k, "score": v["max_score"], "techniques": len(v["techniques"])}
                 for k, v in self.campaigns.items()],
                key=lambda x: x["score"], reverse=True,
            )[:5],
        }


async def activate_soc_lead(config: dict, threat_queue) -> SOCLead:
    lead = SOCLead()
    return lead
