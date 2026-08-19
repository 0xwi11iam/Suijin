"""
suijin/core/blue/ai_engine.py — AI-powered request analysis engine.

Takes anomalous requests, sends them to the LLM with full endpoint context,
and returns structured reasoning + verdict + action decisions.

Also manages the baseline normalization learning — benign verdicts get
added to the normal pattern pool so future identical requests skip the AI.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path


def _active_model():
    from suijin.core.red.config_loader import active_model as _fn

    return _fn


def _default_model():
    from suijin.modules.platform.lib.constants import DEFAULT_MODEL as _v

    return _v


def _risk_high():
    from suijin.modules.platform.lib.constants import RISK_HIGH as _v

    return _v


@dataclass
class AIAnalysisResult:
    """Structured result from AI analysis of a single request."""

    request_id: int
    method: str
    path: str
    ip: str
    body: str = ""
    headers: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)

    # AI output
    reasoning: str = ""  # Full untruncated AI reasoning
    attack_analysis: str = ""  # What the attacker is trying to do
    attacker_assessment: str = ""  # Skill level, tools, persistence
    verdict: str = "NOT FLAGGED"  # FLAGGED or NOT FLAGGED
    score: int = 1  # 1-10 threat score
    action: str = ""  # What was done (code change, block, deceive, log)
    commands_run: list = field(default_factory=list)
    code_changes: list = field(default_factory=list)

    # Metadata
    llm_model: str = ""
    llm_cost_usd: float = 0.0
    analysis_time_ms: float = 0.0


class BlueAIEngine:
    """AI analysis engine for blue team request investigation."""

    def __init__(self, config: dict, endpoint_context: dict = None):
        self.config = config
        self.endpoint_context = endpoint_context or {}
        self.analysis_history: list[AIAnalysisResult] = []
        self.total_cost_usd = 0.0
        self.total_analyses = 0
        self._lock = __import__("threading").Lock()

    def build_analysis_prompt(self, request: dict, endpoint_info: dict, subagent_notes: str = "") -> str:
        """Build a focused analysis prompt with full context for AI decision-making."""
        method = request.get("method", "GET")
        path = request.get("path", "/")
        ip = request.get("ip", "0.0.0.0")
        body = str(request.get("body", ""))
        ua = request.get("user_agent", "")
        query = request.get("query", {})
        headers = request.get("headers", {})

        full_url = f"{method} {path}"
        if query:
            params = "&".join(f"{k}={v}" for k, v in query.items())
            full_url += f"?{params}"

        # ── Gather attacker history from knowledge graph ──
        attacker_context = ""
        try:
            from suijin.modules.blueteam.lib.blue.knowledge_graph import get_kg

            kg = get_kg()
            hist = kg.get_attacker_history(ip)
            if hist.get("attacks"):
                attacker_context = f"\nATTACKER HISTORY FOR {ip}:\n"
                attacker_context += f"  Previous flags: {hist.get('total_flags', 0)}\n"
                for atk in hist["attacks"][-5:]:
                    attacker_context += (
                        f"  - {atk.get('attack_type', '?')} on {atk.get('path', '?')} (score {atk.get('score', '?')})\n"
                    )
                for defense in hist.get("defenses", [])[-3:]:
                    attacker_context += (
                        f"  Defense deployed: {defense.get('type', '?')} — {defense.get('detail', '?')}\n"
                    )
        except Exception:
            import traceback

            traceback.print_exc()
        prompt = f"""SECURITY ALERT — You must classify this request as FLAGGED or NOT FLAGGED.

REQUEST:
  {method} {full_url}
  IP: {ip}
  UA: {ua}
  Headers: {json.dumps(headers)}
  Body: {body if body else "(empty)"}

ENDPOINT: {endpoint_info.get("framework", "unknown")}, auth={endpoint_info.get("auth_required", "unknown")}
Handler code:
```
{endpoint_info.get("handler_code", "N/A")}
```
{attacker_context}
SUBAGENT: {subagent_notes if subagent_notes else "None"}

CLASSIFY THIS REQUEST. If it contains ANY attack pattern (SQL injection, XSS,
command injection, path traversal, SSTI, XXE, mass assignment, auth bypass,
scanner user-agent, JWT manipulation, etc.), verdict MUST be FLAGGED.

FLAGGED example response:
{{"attack_analysis":"SQL injection in username field using OR 1=1 bypass","attacker_assessment":"Automated scanner, low skill","verdict":"FLAGGED","score":8,"reasoning":"Classic SQLi pattern. High confidence. Should tarpit or block.","action":"DECEIVE","action_detail":"Tarpit IP and deploy honeypot","commands_to_run":["echo '{{\\\"169.254.0.1\\\":{{\\\"delay\\\":5}}}}' > /tmp/blue_tarpit.json"],"code_changes":[]}}

NOT FLAGGED example (only for truly benign traffic):
{{"attack_analysis":"Normal variation of login flow","attacker_assessment":"Legitimate user","verdict":"NOT FLAGGED","score":1,"reasoning":"No attack patterns detected. Standard POST to login.","action":"LOG","action_detail":"","commands_to_run":[],"code_changes":[]}}

Respond ONLY with valid JSON. No markdown, no explanation outside JSON."""
        return prompt

    async def analyze_request(
        self,
        request: dict,
        endpoint_info: dict,
        subagent_notes: str = "",
        request_id: int = 0,
    ) -> AIAnalysisResult:
        """Send an anomalous request to the LLM for deep analysis."""
        from suijin.modules.agent.lib.prompts.blue_system import BLUE_SYSTEM_PROMPT
        from suijin.modules.providers.lib import generate, get_usage

        t0 = time.time()

        analysis_prompt = self.build_analysis_prompt(request, endpoint_info, subagent_notes)

        messages = [
            {"role": "system", "content": BLUE_SYSTEM_PROMPT},
            {"role": "user", "content": analysis_prompt},
        ]

        result = AIAnalysisResult(
            request_id=request_id,
            method=request.get("method", "GET"),
            path=request.get("path", "/"),
            ip=request.get("ip", "0.0.0.0"),
            body=str(request.get("body", "")),
            headers=request.get("headers", {}),
            query=request.get("query", {}),
        )

        try:
            # Call the LLM — run in thread to avoid blocking event loop
            raw_response = await asyncio.to_thread(
                generate,
                messages,
                self.config,
                temperature=0.3,
                max_tokens=2000,
                retries=2,
            )

            # Check for API error strings before parsing
            if not raw_response or raw_response.startswith("Error:"):
                raise RuntimeError(f"AI provider error: {raw_response}")

            # Parse JSON from response
            parsed = self._parse_llm_response(raw_response)

            result.reasoning = parsed.get("reasoning", raw_response)
            result.attack_analysis = parsed.get("attack_analysis", "")
            result.attacker_assessment = parsed.get("attacker_assessment", "")
            result.verdict = parsed.get("verdict", "NOT FLAGGED")
            result.score = int(parsed.get("score", 1))
            result.action = parsed.get("action", "LOG")
            result.commands_run = parsed.get("commands_to_run", [])
            result.code_changes = parsed.get("code_changes", [])

            # Record cost
            usage = get_usage()
            result.llm_cost_usd = usage.get("est_cost_usd", 0.0)
            result.llm_model = _active_model()(self.config) or _default_model()

        except Exception as e:
            result.reasoning = f"AI call failed: {e}"
            result.verdict = "FLAGGED"
            result.score = _risk_high()
            result.action = "REVIEW"
            result.attack_analysis = f"AI engine unavailable — request flagged for manual review. Error: {e}"
            result.commands_run = []
            result.code_changes = []

        result.analysis_time_ms = (time.time() - t0) * 1000

        with self._lock:
            self.analysis_history.append(result)
            self.total_analyses += 1
            self.total_cost_usd += result.llm_cost_usd

        return result

    def _parse_llm_response(self, raw: str) -> dict:
        """Parse JSON from LLM response, handling markdown code blocks."""
        if not raw:
            return {}

        # Try direct JSON
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code blocks
        import re

        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding first { ... } block
        brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return {"reasoning": raw}

    def execute_actions(self, result: AIAnalysisResult, target_path: str) -> list:
        """Execute the commands and code changes from an analysis result.

        Commands are run via subprocess. Code changes with new_content are written
        to the target codebase filesystem.
        """
        import subprocess

        executed = []

        # Run commands
        for cmd in result.commands_run:
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=target_path,
                )
                executed.append(
                    {
                        "type": "command",
                        "command": cmd,
                        "stdout": proc.stdout[:500],
                        "stderr": proc.stderr[:500],
                        "exit_code": proc.returncode,
                    }
                )
            except subprocess.TimeoutExpired:
                executed.append(
                    {
                        "type": "command",
                        "command": cmd,
                        "error": "Timeout after 30s",
                    }
                )
            except Exception as e:
                executed.append(
                    {
                        "type": "command",
                        "command": cmd,
                        "error": str(e),
                    }
                )

        # Apply code changes — actually write files
        for change in result.code_changes:
            file_rel = change.get("file", "")
            description = change.get("change", "")
            new_content = change.get("new_content", "")

            if not file_rel:
                continue

            full_path = Path(target_path) / file_rel

            if new_content:
                # Write the actual new code to disk
                try:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(new_content)
                    executed.append(
                        {
                            "type": "code_change",
                            "file": str(full_path),
                            "description": description,
                            "written": True,
                            "bytes": len(new_content),
                        }
                    )
                except Exception as e:
                    executed.append(
                        {
                            "type": "code_change",
                            "file": str(full_path),
                            "description": description,
                            "written": False,
                            "error": str(e),
                        }
                    )
            else:
                # No content to write — just record the suggestion
                executed.append(
                    {
                        "type": "code_change",
                        "file": str(full_path),
                        "description": description,
                        "written": False,
                        "note": "No new_content provided — change not applied",
                    }
                )

        return executed
