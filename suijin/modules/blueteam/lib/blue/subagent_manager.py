"""
suijin/core/blue/subagent_manager.py — Endpoint subagent orchestration.

After codebase analysis, deploys one AI subagent per endpoint. Each subagent:
- Analyzes its handler code for vulnerabilities
- Ranks risk level (1-10)
- Plans defensive measures
- Watches traffic to its endpoint
- Reports anomalies to the main coordinating agent
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _risk_high():
    from suijin.modules.platform.lib.constants import RISK_HIGH as _v

    return _v


@dataclass
class EndpointSubagent:
    """A dedicated AI subagent watching a single endpoint with pre-loaded intelligence."""

    agent_id: str
    endpoint: dict
    rank: int
    risk_score: int = 1
    vulnerability_notes: str = ""
    defense_plan: str = ""
    normal_patterns: list = field(default_factory=list)
    anomalies_reported: int = 0
    attacks_blocked: int = 0
    last_analysis: str = ""
    status: str = "initializing"
    # Pre-loaded engineering assets — ready to deploy when attack hits
    handler_code: str = ""  # Full handler source code
    honeypot_code: str = ""  # Ready-to-deploy honeypot version of endpoint
    patch_code: str = ""  # Ready-to-deploy fix for vulnerability
    deception_response: str = ""  # Ready-to-deploy fake response data
    framework: str = ""  # flask, fastapi, django, express, etc.


class SubagentManager:
    """Manages the lifecycle of all endpoint subagents."""

    def __init__(self, config: dict, target_path: str):
        self.config = config
        self.target_path = target_path
        self.subagents: dict[str, EndpointSubagent] = {}
        self._lock = __import__("threading").Lock()

    def deploy_all(self, endpoints: list) -> list[EndpointSubagent]:
        """Deploy one subagent per discovered endpoint. No artificial cap."""
        deployed = []
        for i, ep in enumerate(endpoints):
            path = ep.get("path", "/")
            agent_id = hashlib.md5(path.encode()).hexdigest()[:8]
            rank = i + 1

            sa = EndpointSubagent(
                agent_id=f"subagent-{rank:02d}",
                endpoint=ep,
                rank=rank,
            )
            self.subagents[agent_id] = sa
            deployed.append(sa)

        return deployed

    def find_for_request(self, path: str) -> Optional[EndpointSubagent]:
        """Find the subagent responsible for a given request path."""
        # Exact match first
        for sa in self.subagents.values():
            if sa.endpoint.get("path") == path:
                return sa

        # Prefix match — /api/users/42 matches /api/users/<int:uid>.
        # BF0: boundary check — the raw prefix also matched sibling paths
        # like /api/users_export. Consume the variable segment too: after
        # the prefix, the next path segment must be a single component.
        for sa in self.subagents.values():
            ep_path = sa.endpoint.get("path", "")
            # Convert Flask/Express patterns to simple prefixes
            prefix = ep_path.split("<")[0].rstrip("/")
            if not prefix or not path.startswith(prefix):
                continue
            if "<" in ep_path:
                rest = path[len(prefix):].lstrip("/")
                next_fixed = ep_path.split("<", 1)[1].split(">", 1)[-1]  # after the var
                consumed = rest.split("/", 1)[0]
                after = rest[len(consumed):]
                expected = next_fixed  # e.g. '' or '/detail'
                if after != expected:
                    continue  # variable segment overran into a sibling path
            elif path != prefix:
                continue  # static route only matches itself
            return sa

        return None

    async def analyze_endpoint(self, sa: EndpointSubagent) -> EndpointSubagent:
        """Deep analysis — read the ENTIRE source file, no truncation."""
        from suijin.modules.agent.lib.prompts.blue_system import BLUE_SYSTEM_PROMPT
        from suijin.modules.providers.lib import generate

        ep = sa.endpoint
        file_path = ep.get("file", "")
        framework = ep.get("framework", "unknown")

        # Read the ENTIRE source file — every line, no truncation
        if file_path:
            try:
                handler_code = Path(file_path).read_text(errors="ignore")
            except Exception:
                handler_code = f"File: {file_path} (could not read)"
        else:
            handler_code = "No source file available"

        sa.handler_code = handler_code
        sa.framework = framework

        prompt = f"""ENDPOINT DEFENSE ENGINEERING — Subagent #{sa.rank}

You are the autonomous defender for this endpoint. You have FULL authority to
write code, deploy honeypots, modify the application, and deceive attackers.

ENDPOINT:
  Method: {ep.get("method", "ANY")}
  Path: {ep.get("path", "/")}
  Framework: {framework}
  File: {ep.get("file", "unknown")}

FULL HANDLER CODE:
```
{handler_code}
```

YOUR ENGINEERING TASKS:

1. VULNERABILITY ANALYSIS: What can an attacker exploit here? SQLi? XSS? IDOR?
   Auth bypass? Command injection? Look at every line.

2. HONEYPOT ENGINEERING: Write a complete fake version of this endpoint that
   looks real but traps attackers. Include canary tokens (fake API keys,
   fake credentials, tracking IDs). Return the FULL Python/JS code ready to
   deploy as a new route. The honeypot should log everything the attacker does.

3. PATCH: Write the fixed version of this handler. Parameterize queries,
   add input validation, add auth checks. Return the FULL corrected code.

4. DECEPTION TEMPLATES: What fake data should we return to deceive attackers?
   Fake users, fake flags, fake tokens that trigger alerts when used.

5. NORMAL PATTERNS: What does legitimate traffic look like for this endpoint?
   Expected HTTP methods, parameter names, body structure.

Respond in JSON — provide ALL code as FULL strings ready to deploy:
{{
  "risk_score": 1-10,
  "vulnerability_notes": "Detailed vulnerability analysis",
  "honeypot_code": "FULL honeypot endpoint code as a string. Include route decorator, handler function, canary tokens, and logging.",
  "patch_code": "FULL fixed handler code as a string. The corrected version with parameterized queries, validation, auth checks.",
  "deception_response": "JSON string of fake data to return to attackers (fake users, fake flags, canary tokens)",
  "defense_plan": "Step-by-step plan for defending this endpoint",
  "normal_patterns": ["expected_methods", "expected_params", "expected_body_structure"]
}}"""

        messages = [
            {"role": "system", "content": BLUE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            raw = await asyncio.to_thread(
                generate,
                messages,
                self.config,
                temperature=0.3,
                max_tokens=2500,
                retries=2,
            )
            parsed = self._parse_json(raw)
            sa.risk_score = int(parsed.get("risk_score", 1))
            sa.vulnerability_notes = parsed.get("vulnerability_notes", raw)
            sa.defense_plan = parsed.get("defense_plan", "")
            sa.honeypot_code = parsed.get("honeypot_code", "")
            sa.patch_code = parsed.get("patch_code", "")
            sa.deception_response = parsed.get("deception_response", "")
            sa.normal_patterns = parsed.get("normal_patterns", [])
            sa.status = "active"
            sa.last_analysis = time.strftime("%H:%M:%S")
        except Exception as e:
            sa.vulnerability_notes = f"AI analysis unavailable: {e}"
            sa.risk_score = 5
            sa.status = "active"
            # Fallback: basic pattern-based intelligence without AI
            self._fallback_analysis(sa)

        return sa

    def _fallback_analysis(self, sa: EndpointSubagent):
        """Basic pattern-based analysis when AI is unavailable."""
        code = sa.handler_code.lower() if sa.handler_code else ""
        path = sa.endpoint.get("path", "/").lower()
        vulns = []

        if any(kw in code for kw in ["execute(", 'f"select', "f'select", "+ request.", "eval(", "exec("]):
            vulns.append("SQLi or code injection risk detected")
            sa.risk_score = max(sa.risk_score, 7)
        if any(kw in code for kw in [".popen(", "subprocess.", "os.system(", "shell=true"]):
            vulns.append("Command injection risk detected")
            sa.risk_score = max(sa.risk_score, 8)
        if any(kw in path for kw in ["admin", "config", "debug"]):
            vulns.append("Sensitive endpoint — likely needs auth")
            sa.risk_score = max(sa.risk_score, 6)
        if any(kw in code for kw in ["request.args", "request.form", "request.json", "req.query", "req.body"]):
            vulns.append("User input accepted — validate all parameters")

        sa.vulnerability_notes = (
            "; ".join(vulns) if vulns else "No obvious patterns detected — full AI analysis recommended"
        )
        sa.defense_plan = "Monitor all requests to this endpoint. Validate input. Apply rate limiting."
        sa.normal_patterns = [sa.endpoint.get("method", "GET")]

    async def analyze_all_endpoints(self) -> list[EndpointSubagent]:
        """Analyze all deployed subagents in parallel batches."""
        batch_size = 5
        agents = list(self.subagents.values())
        results = []

        for i in range(0, len(agents), batch_size):
            batch = agents[i : i + batch_size]
            tasks = [self.analyze_endpoint(sa) for sa in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in batch_results:
                if isinstance(r, EndpointSubagent):
                    results.append(r)
            if i + batch_size < len(agents):
                await asyncio.sleep(0.5)  # Rate limit between batches

        return results

    def get_subagent_notes(self, path: str) -> str:
        """Get the subagent's intelligence notes for a given endpoint path."""
        sa = self.find_for_request(path)
        if not sa:
            return ""

        notes = f"""  Subagent #{sa.rank}: {sa.agent_id}
  Risk Score: {sa.risk_score}/10
  Status: {sa.status}
  Vulnerabilities: {sa.vulnerability_notes}
  Defense Plan: {sa.defense_plan}
  Anomalies Reported: {sa.anomalies_reported}"""

        return notes

    def record_anomaly(self, path: str, verdict: str):
        """Record that a subagent detected an anomaly."""
        sa = self.find_for_request(path)
        if sa:
            with self._lock:
                sa.anomalies_reported += 1
                if verdict == "FLAGGED":
                    sa.attacks_blocked += 1

    def get_summary(self) -> dict:
        """Get a summary of all subagent statuses."""
        agents = list(self.subagents.values())
        return {
            "total": len(agents),
            "active": sum(1 for a in agents if a.status == "active"),
            "high_risk": sum(1 for a in agents if a.risk_score >= _risk_high()),
            "total_anomalies": sum(a.anomalies_reported for a in agents),
            "total_blocked": sum(a.attacks_blocked for a in agents),
            "by_risk": sorted(
                [
                    {
                        "rank": a.rank,
                        "path": a.endpoint.get("path", "/"),
                        "risk": a.risk_score,
                        "anomalies": a.anomalies_reported,
                    }
                    for a in agents
                ],
                key=lambda x: x["risk"],
                reverse=True,
            ),
        }

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Parse JSON from LLM response."""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        import re

        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {}
