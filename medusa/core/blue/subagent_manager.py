"""
medusa/core/blue/subagent_manager.py — Endpoint subagent orchestration.

After codebase analysis, deploys one AI subagent per endpoint. Each subagent:
- Analyzes its handler code for vulnerabilities
- Ranks risk level (1-10)
- Plans defensive measures
- Watches traffic to its endpoint
- Reports anomalies to the main coordinating agent
"""
import json, asyncio, time, hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EndpointSubagent:
    """A dedicated AI subagent watching a single endpoint."""
    agent_id: str
    endpoint: dict
    rank: int                      # 1-based ordering in the feed
    risk_score: int = 1            # 1-10 risk assessment
    vulnerability_notes: str = ""  # AI analysis of what could go wrong
    defense_plan: str = ""         # Planned defensive measures
    normal_patterns: list = field(default_factory=list)  # Known-safe patterns
    anomalies_reported: int = 0
    attacks_blocked: int = 0
    last_analysis: str = ""
    status: str = "initializing"


class SubagentManager:
    """Manages the lifecycle of all endpoint subagents."""

    def __init__(self, config: dict, target_path: str):
        self.config = config
        self.target_path = target_path
        self.subagents: dict[str, EndpointSubagent] = {}
        self._lock = __import__('threading').Lock()

    def deploy_all(self, endpoints: list) -> list[EndpointSubagent]:
        """Deploy one subagent per discovered endpoint."""
        deployed = []
        for i, ep in enumerate(endpoints[:50]):  # Cap at 50 subagents
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

        # Prefix match — /api/users/42 matches /api/users/<int:uid>
        for sa in self.subagents.values():
            ep_path = sa.endpoint.get("path", "")
            # Convert Flask/Express patterns to simple prefixes
            prefix = ep_path.split("<")[0].rstrip("/")
            if prefix and path.startswith(prefix):
                return sa

        return None

    async def analyze_endpoint(self, sa: EndpointSubagent) -> EndpointSubagent:
        """Have the AI analyze this endpoint for vulnerabilities and plan defense."""
        from medusa.tools.providers import generate
        from medusa.prompts.blue_system import BLUE_SYSTEM_PROMPT

        ep = sa.endpoint
        file_path = ep.get("file", "")
        handler_code = ""

        # Read the actual handler code if available
        if file_path:
            try:
                code = Path(file_path).read_text(errors="ignore")
                # Extract the function near the route line
                lines = code.split("\n")
                line_num = ep.get("line", 0)
                start = max(0, line_num - 2)
                end = min(len(lines), line_num + 30)
                handler_code = "\n".join(lines[start:end])
            except Exception:
                handler_code = f"File: {file_path} (could not read)"

        prompt = f"""ENDPOINT ANALYSIS — Subagent #{sa.rank}

You are assigned to defend this endpoint. Analyze it thoroughly.

ENDPOINT:
  Method: {ep.get('method', 'ANY')}
  Path: {ep.get('path', '/')}
  Framework: {ep.get('framework', 'unknown')}
  File: {ep.get('file', 'unknown')}
  Line: {ep.get('line', 'unknown')}

HANDLER CODE:
```
{handler_code[:1500]}
```

YOUR TASKS:
1. Identify potential vulnerabilities in this endpoint (SQLi, XSS, IDOR, auth bypass, etc.)
2. Rate the risk level 1-10 (10 = critical, publicly accessible, handles sensitive data)
3. Plan defensive measures specific to this endpoint
4. Define what normal traffic looks like for this endpoint
5. Define what anomalous traffic patterns to watch for

Respond in JSON:
{{
  "risk_score": 1-10,
  "vulnerability_notes": "Detailed analysis of potential vulnerabilities",
  "defense_plan": "Specific defensive measures for this endpoint",
  "normal_patterns": ["pattern1", "pattern2"],
  "anomaly_watchlist": ["anomaly1", "anomaly2"]
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
                max_tokens=1500,
                retries=2,
            )

            parsed = self._parse_json(raw)
            sa.risk_score = int(parsed.get("risk_score", 1))
            sa.vulnerability_notes = parsed.get("vulnerability_notes", raw[:500])
            sa.defense_plan = parsed.get("defense_plan", "")
            sa.normal_patterns = parsed.get("normal_patterns", [])
            sa.status = "active"
            sa.last_analysis = time.strftime("%H:%M:%S")

        except Exception as e:
            sa.vulnerability_notes = f"Analysis failed: {e}"
            sa.risk_score = 5
            sa.status = "active"

        return sa

    async def analyze_all_endpoints(self) -> list[EndpointSubagent]:
        """Analyze all deployed subagents in parallel batches."""
        batch_size = 5
        agents = list(self.subagents.values())
        results = []

        for i in range(0, len(agents), batch_size):
            batch = agents[i:i + batch_size]
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
  Vulnerabilities: {sa.vulnerability_notes[:300]}
  Defense Plan: {sa.defense_plan[:300]}
  Anomalies Reported: {sa.anomalies_reported}
  Normal Patterns: {', '.join(sa.normal_patterns[:5])}"""

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
            "high_risk": sum(1 for a in agents if a.risk_score >= 7),
            "total_anomalies": sum(a.anomalies_reported for a in agents),
            "total_blocked": sum(a.attacks_blocked for a in agents),
            "by_risk": sorted(
                [{"rank": a.rank, "path": a.endpoint.get("path", "/"),
                  "risk": a.risk_score, "anomalies": a.anomalies_reported}
                 for a in agents],
                key=lambda x: x["risk"], reverse=True,
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
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {}
