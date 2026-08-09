"""
medusa/core/blue/tui/feed.py — Live traffic feed dispatcher.

Routes each incoming request to the appropriate tier:
  NORMAL       — matches known-safe baseline, one-line display, zero AI cost
  ANOMALOUS    — deviates from baseline, sent to AI for analysis, one-line display
  INVESTIGATED — attack pattern detected OR AI flagged, full rich panel

Pre-AI fast path: pattern matching catches obvious SQLi/XSS/SSRF before
spending an AI call. If the pattern detector fires, the request gets the
full INVESTIGATED panel immediately, actual deception/blocking is deployed,
and the action is recorded in the shared knowledge graph.
"""
import asyncio, json, time, re, os
from typing import Optional
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel

from medusa.core.blue.ai_engine import BlueAIEngine, AIAnalysisResult
from medusa.core.blue.subagent_manager import SubagentManager
from medusa.core.blue.tui.request_panel import (
    render_normal_line,
    render_anomalous_line,
    render_investigated_request,
    render_subagent_assignment,
)
from medusa.core.blue.knowledge_graph import get_kg

console = Console()


# ── Pre-AI attack pattern detection ──────────────────────────────────
# Catches obvious SQLi, XSS, SSRF, path traversal, command injection
# BEFORE spending an AI call. If any pattern fires with confidence,
# the request is INVESTIGATED immediately and still sent to AI.

_ATTACK_PATTERNS = [
    # (name, regex, weight)
    ("SQL Injection", r"(?i)(union\s+select|'\s*or\s+'[^']*'\s*=\s*'|'\s*or\s+\d+\s*=\s*\d+|'\s*--|;\s*drop\s+table|\bselect\b.*\bfrom\b.*\bwhere\b)", 5),
    ("SQL Injection (blind)", r"(?i)('\s*or\s+sleep\s*\(|'\s*and\s+sleep\s*\(|benchmark\s*\(|'\s*or\s+pg_sleep)", 5),
    ("XSS", r"(?i)(<script[^>]*>|onerror\s*=|javascript\s*:|<img[^>]+onerror|<svg[^>]+onload|alert\s*\(|prompt\s*\()", 5),
    ("Path Traversal", r"(?:\.\./|\.\.\\|/etc/passwd|/etc/shadow|C:\\Windows\\|/winnt/|boot\.ini)", 4),
    ("SSRF", r"(?:169\.254\.169\.254|metadata\.google\.internal|100\.64\.0\.\d+|\.cloud/metadata)", 5),
    ("Command Injection", r"(?i)(;\s*(id|whoami|uname|cat\s+/etc|ls\s+-la|pwd|wget\s+|curl\s+)\b|\|\s*(id|whoami|cat)|`[^`]{2,}`|\$\([^)]{2,}\))", 5),
    ("SSTI", r"(?i)(\{\{.*\}\}|\$\{.*\}|<%=.*%>|#\{.*\}|\{\%.*\%\})", 4),
    ("XXE", r"(?i)(<!ENTITY\s+\w+\s+SYSTEM|<!DOCTYPE\s+\w+\s+\[)", 5),
    ("JWT Attack", r"(?i)(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})", 3),
    ("Deserialization", r"(?i)(O:\d+:\"[^\"]+\":\d+:|pickle\.loads|yaml\.load\s*\(|marshal\.loads)", 5),
    ("LDAP Injection", r"(?i)(\(\s*&\s*\(|\*\s*\)\(\s*\|)", 4),
    ("NoSQL Injection", r"(?i)(\$\s*(ne|gt|lt|gte|lte|in|nin|regex|where|or|and)\s*:)", 4),
    ("Scanner User-Agent", r"(?i)(nmap|sqlmap|nikto|dirbuster|gobuster|burpsuite|acunetix|nessus|openvas|zap|w3af)", 4),
    # New patterns
    ("Mass Assignment", r"(?:\"role\"\s*:\s*\"(?:admin|root|superuser)\"|\"is_admin\"\s*:\s*true|\"isAdmin\"\s*:\s*true)", 4),
    ("Auth Bypass Header", r"(?:X-Admin:\s*true|X-Role:\s*admin|X-Forwarded-For:\s*127\.0\.0\.1|X-Original-URL:|X-Rewrite-URL:)", 5),
    ("Brute Force", r"(?i)(?:password.*password|Hydra|Medusa|Ncrack|patator|crowbar)", 3),
    ("File Inclusion", r"(?i)(php://filter|php://input|data://text|expect://|file:///etc)", 5),
    ("GraphQL Attack", r"(?i)(__schema|__type|__typename|fragment|query\s*\{|mutation\s*\{)", 3),
]


def _detect_obvious_attack(request: dict) -> dict:
    """Fast pre-AI pattern matching. Returns score + patterns found.

    This runs BEFORE any AI call so obvious attacks get immediate attention.
    Returns {"score": int, "patterns": [(name, weight), ...]}
    """
    body = str(request.get("body", ""))
    ua = str(request.get("user_agent", ""))
    path = request.get("path", "/")
    query = str(request.get("query", ""))
    headers = str(request.get("headers", ""))

    # Combine all text to scan
    scan_text = f"{body} {ua} {path} {query} {headers}"

    score = 1
    patterns_found = []

    for name, pattern, weight in _ATTACK_PATTERNS:
        if re.search(pattern, scan_text):
            score = min(10, score + weight)
            patterns_found.append((name, weight))

    return {"score": score, "patterns": patterns_found}


@dataclass
class FeedConfig:
    """Configuration for the live feed behavior."""
    baseline_requests: int = 25      # How many requests to establish baseline
    ai_analysis_enabled: bool = True # Whether to send anomalies to AI
    show_all_normals: bool = True    # Show every normal request line
    pattern_score_threshold: int = 5 # Auto-DECEIVE/BLOCK if pattern score >= 5
    panel_width: int = 80


class LiveFeed:
    """Manages the live traffic feed with smart tier routing."""

    def __init__(
        self,
        ai_engine: BlueAIEngine,
        subagent_manager: SubagentManager,
        config: FeedConfig = None,
    ):
        self.ai_engine = ai_engine
        self.subagent_manager = subagent_manager
        self.config = config or FeedConfig()
        self.request_count = 0
        self.baseline_established = False
        self.pending_analyses: dict[int, asyncio.Task] = {}
        self._analysis_queue = asyncio.Queue()

    async def process_request(self, request: dict) -> Optional[AIAnalysisResult]:
        """Process a single incoming request through the tier system.

        Flow:
          1. Baseline mode → all NORMAL (building patterns)
          2. Known normal → NORMAL (one-line, zero cost)
          3. Pre-AI pattern check → if SQLi/XSS/etc detected → BLOCK/DECEIVE now
          4. AI analysis → INVESTIGATED (flagged) or ANOMALOUS (benign)
          5. AI failure → shown as ANOMALOUS with error visible
        """
        self.request_count += 1
        rid = self.request_count
        path = request.get("path", "/")
        method = request.get("method", "GET")
        ip = request.get("ip", "0.0.0.0")

        # Find the subagent responsible for this endpoint
        sa = self.subagent_manager.find_for_request(path)

        # ── BASELINE MODE: first N requests establish normal ──
        if not self.baseline_established:
            if rid >= self.config.baseline_requests:
                self.baseline_established = True
                console.print(f"\n  [bold green]BASELINE ESTABLISHED[/bold green] [dim]({rid} requests)[/dim]")
                console.print(f"  [dim]AI analysis now active for anomalous requests[/dim]\n")
            else:
                if self.config.show_all_normals:
                    if sa:
                        render_subagent_assignment(rid, path, sa.rank, sa.agent_id)
                    render_normal_line(rid, method, path, ip)
                return None

        # ── AFTER BASELINE: check if this is a known normal ──
        from medusa.core.blue.traffic.normalizer import get_global_normalizer
        normalizer = get_global_normalizer()

        if normalizer and normalizer.is_known_normal(request):
            if self.config.show_all_normals:
                if sa:
                    render_subagent_assignment(rid, path, sa.rank, sa.agent_id)
                render_normal_line(rid, method, path, ip)
            return None

        # ── PRE-AI FAST PATH: pattern-based attack detection ──
        attack_check = _detect_obvious_attack(request)

        if sa:
            render_subagent_assignment(rid, path, sa.rank, sa.agent_id)

        if attack_check["score"] >= self.config.pattern_score_threshold:
            return await self._handle_attack_detected(
                request, attack_check, sa, rid, path, method, ip, normalizer)

        # ── NO PATTERN MATCH: send to AI for analysis ──
        if not self.config.ai_analysis_enabled:
            render_anomalous_line(rid, method, path, ip, score=attack_check["score"], flagged=False)
            return None

        result = await self._call_ai(request, sa, rid, path, method, ip)
        if result is None:
            return None

        if result.verdict == "FLAGGED":
            self.subagent_manager.record_anomaly(path, "FLAGGED")
            render_investigated_request(result)
            self._execute_ai_decision(result, ip, [], result.score)
            return result
        else:
            if normalizer:
                normalizer.add_to_baseline(request)
            render_anomalous_line(rid, method, path, ip, score=result.score, flagged=False)
            return result

    # ── Attack response — actually DO something ────────────────────────
    _flagged_ips: dict = {}  # ip -> count of times flagged

    async def _handle_attack_detected(self, request, attack_check, sa, rid, path, method, ip, normalizer):
        """Pattern matched — let AI decide the actual response.

        No hardcoded thresholds. The AI receives full context (KG state,
        attacker history, available tools) and decides: BLOCK, DECEIVE,
        PATCH, LOG, REDIRECT, or any custom response.
        """
        pattern_names = [p[0] for p in attack_check["patterns"]]
        score = attack_check["score"]
        body = str(request.get("body", ""))

        # Track repeat offenders
        prev = self._flagged_ips.get(ip, 0)
        self._flagged_ips[ip] = prev + 1
        effective_score = min(10, score + prev)

        # Record in knowledge graph
        kg = get_kg()
        kg.add_attack(ip, path, pattern_names[0], effective_score, body)

        # Show initial panel — pattern match detected
        console.print(f"\n  [bold red]ATTACK DETECTED[/bold red] [dim]({', '.join(pattern_names)} | pattern score {effective_score}/10)[/dim]")
        result = AIAnalysisResult(
            request_id=rid, method=method, path=path, ip=ip,
            body=body, headers=request.get("headers", {}), query=request.get("query", {}),
            reasoning=f"Pattern match: {', '.join(pattern_names)}. Awaiting AI decision.",
            attack_analysis=f"Pattern detector found: {', '.join(pattern_names)}. "
                           f"IP flagged {prev + 1} time(s). Effective score: {effective_score}/10.",
            attacker_assessment=f"IP {ip} — {prev + 1} flags. Patterns: {', '.join(pattern_names)}.",
            verdict="FLAGGED", score=effective_score,
            action="ANALYZING",
        )
        self.subagent_manager.record_anomaly(path, "FLAGGED")
        render_investigated_request(result)

        # ── AI decides the actual response ──
        if self.config.ai_analysis_enabled:
            ai_result = await self._call_ai(request, sa, rid, path, method, ip)
            if ai_result and ai_result.verdict == "FLAGGED":
                # Execute whatever the AI decided
                console.print(f"\n  [bold cyan]AI DECISION[/bold cyan] [dim](#{rid})[/dim] — {ai_result.action}")
                self._execute_ai_decision(ai_result, ip, pattern_names, effective_score)
                kg.add_defense(ip, ai_result.action.lower(), ai_result.reasoning[:200])
                kg.add_intelligence(f"ai-decision-{rid}", ai_result.reasoning[:300])
                kg.save()
                return ai_result
            elif ai_result:
                # AI disagrees with pattern detector — log the disagreement but STILL defend
                console.print(f"  [bold yellow]AI OVERRIDE[/bold yellow] [dim]— AI says {ai_result.verdict} (score {ai_result.score}) but pattern score is {effective_score}[/dim]")
                console.print(f"  [dim]AI reasoning: {ai_result.reasoning[:300]}[/dim]")
                console.print(f"  [bold yellow]Applying fallback defense despite AI override[/bold yellow]")
                # NEVER add pattern-matched attacks to normal baseline
                self._apply_tarpit(ip, effective_score, pattern_names)
                kg.add_defense(ip, "tarpit", f"Fallback — AI said {ai_result.verdict} but pattern score {effective_score}")
                kg.save()
                return result
            else:
                # AI failed — fall back to tarpit
                console.print(f"  [yellow]AI unavailable — deploying default tarpit for {ip}[/yellow]")
                self._apply_tarpit(ip, effective_score, pattern_names)
                kg.add_defense(ip, "tarpit", f"Fallback — AI unavailable, score {effective_score}")
                kg.save()
        else:
            # AI disabled — tarpit
            self._apply_tarpit(ip, effective_score, pattern_names)
            kg.add_defense(ip, "tarpit", f"AI disabled, score {effective_score}")
            kg.save()
            self._apply_tarpit(ip, effective_score, pattern_names)
            kg.add_defense(ip, "tarpit", f"AI disabled, score {effective_score}")
            kg.save()

        return result

    # ── Execute AI's decision ────────────────────────────────────────
    TARPIT_FILE = "/tmp/blue_tarpit.json"

    def _execute_ai_decision(self, result: AIAnalysisResult, ip: str, patterns: list, score: int):
        """Execute whatever the AI decided — commands, code changes, deception."""
        target_path = getattr(self.ai_engine, 'target_path', '')

        # Execute commands the AI requested
        if result.commands_run:
            import subprocess
            console.print("  [bold white]Commands:[/bold white]")
            for cmd in result.commands_run:
                try:
                    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, cwd=target_path or None)
                    ok = proc.returncode == 0
                    status = f"[green]OK[/green]" if ok else f"[red]FAIL({proc.returncode})[/red]"
                    console.print(f"    [dim]$[/dim] {cmd} {status}")
                    if proc.stdout.strip():
                        console.print(f"    [dim]  {proc.stdout.strip()[:200]}[/dim]")
                except Exception as e:
                    console.print(f"    [dim]$[/dim] {cmd} [red]ERROR: {e}[/red]")

        # Apply code changes the AI requested
        if result.code_changes and target_path:
            from pathlib import Path
            for cc in result.code_changes:
                file_rel = cc.get("file", "")
                new_content = cc.get("new_content", "")
                if file_rel and new_content:
                    full = Path(target_path) / file_rel
                    try:
                        full.parent.mkdir(parents=True, exist_ok=True)
                        full.write_text(new_content)
                        console.print(f"  [green]PATCHED:[/green] {file_rel} ({len(new_content)} bytes)")
                        console.print(f"  [dim]  {cc.get('change', '')[:120]}[/dim]")
                    except Exception as e:
                        console.print(f"  [red]PATCH FAILED:[/red] {file_rel} — {e}")

        # If AI said DECEIVE or BLOCK, ensure tarpit/blocking is applied
        action = (result.action or "").upper()
        if "DECEIVE" in action or "TARPIT" in action:
            self._apply_tarpit(ip, score, patterns)
        if "BLOCK" in action:
            self._apply_block(ip, score)

        # Show what happened
        action_color = {"BLOCK": "red", "DECEIVE": "yellow", "PATCH": "green", "LOG": "dim"}.get(result.action.upper() if result.action else "", "white")
        console.print(Panel.fit(
            f"[bold {action_color}]ACTION: {result.action}[/bold {action_color}]\n"
            f"[dim]{result.reasoning[:400]}[/dim]",
            border_style=action_color, padding=(1, 2),
        ))

    def _apply_tarpit(self, ip: str, score: int, patterns: list):
        """Apply tarpit — write state file Flask checks on each request."""
        try:
            state = {}
            if os.path.exists(self.TARPIT_FILE):
                state = json.loads(open(self.TARPIT_FILE).read())
            state[ip] = {"delay": min(8.0, 1.0 + score * 0.8), "since": time.time(),
                         "patterns": patterns}
            with open(self.TARPIT_FILE, "w") as f:
                json.dump(state, f)
            console.print(f"  [yellow]TARPIT:[/yellow] {ip} — {state[ip]['delay']:.1f}s delay per request")
        except Exception as e:
            console.print(f"  [red]TARPIT FAILED:[/red] {e}")

    def _apply_block(self, ip: str, score: int):
        """Apply network-level block via pfctl."""
        import subprocess
        try:
            subprocess.run(["pfctl", "-t", "blue_blocked", "-T", "add", ip],
                          capture_output=True, timeout=5)
            console.print(f"  [red]BLOCKED:[/red] {ip} — pfctl rule added")
        except Exception:
            console.print(f"  [dim]BLOCK:[/dim] {ip} — pfctl unavailable, logged only")

    async def _call_ai(self, request, sa, rid, path, method, ip) -> Optional[AIAnalysisResult]:
        """Call the AI engine. Returns None on failure (error already shown)."""
        endpoint_info = sa.endpoint if sa else {"path": path, "method": method}
        subagent_notes = self.subagent_manager.get_subagent_notes(path) if sa else ""

        try:
            return await self.ai_engine.analyze_request(
                request=request,
                endpoint_info=endpoint_info,
                subagent_notes=subagent_notes,
                request_id=rid,
            )
        except Exception as e:
            console.print(f"  [bold red]AI ANALYSIS FAILED[/bold red] [dim]— {e}[/dim]")
            console.print(f"  [dim]  Request #{rid} {method} {path} could not be analyzed.[/dim]")
            console.print(f"  [dim]  Check API key, network, and provider config in medusa/.env[/dim]")
            return None

    def get_stats(self) -> dict:
        """Get current feed statistics."""
        return {
            "total": self.request_count,
            "baseline_established": self.baseline_established,
            "ai_analyses": self.ai_engine.total_analyses,
            "ai_cost": self.ai_engine.total_cost_usd,
            "subagents": self.subagent_manager.get_summary(),
        }
