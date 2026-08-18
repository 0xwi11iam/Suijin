"""
medusa/oracle.py
=================
Causal Reasoning Oracle — stops blind fuzzing loops when the target signals
anomalous behavior and kicks into structured diagnostic mode.

Flow:
  1. detect_anomaly()  — spot 500s, timeouts, body-length deltas, WAF signatures
  2. strip_response()  — keep only headers, status, dynamic reflection; ditch HTML
  3. generate_hypotheses() — 3 concrete backend theories via fast model
  4. verify_hypothesis() — binary test: confirm or disprove with isolate payload
  5. record_finding()  — persist verified constraint to Knowledge Graph

NO hypothesis is accepted as fact without a confirming tool call.
"""

import json
import re
import time
from pathlib import Path

from rich.console import Console

from medusa.modules.loader import load_local_module

console = Console()
BASE_DIR = Path(__file__).resolve().parent

# Lazy — wired at import time by redteamer
_providers = None
_generate = None
_knowledge_graph = None


def set_providers(providers_mod):
    """Inject the caller's providers module so the oracle shares the token accumulator."""
    global _providers, _generate
    _providers = providers_mod
    _generate = providers_mod.generate


def _get_kg():
    global _knowledge_graph
    if _knowledge_graph is None:
        _knowledge_graph = load_local_module("knowledge_graph")
    return _knowledge_graph


# ---------------------------------------------------------------------------
# Response stripping — keep token budget tight
# ---------------------------------------------------------------------------
HTML_STRIP_RE = re.compile(
    r"<(script|style|head|noscript|iframe|svg|link|meta)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
TAG_STRIP_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s{3,}")
SESSION_RE = re.compile(r"(session|token|csrf|nonce|authenticity_token)[=:]\s*[^\s;,&]+", re.IGNORECASE)


def strip_response(response_text, status_code=None, headers=None):
    """Reduce an HTTP response to its diagnostic essence.

    Strips HTML boilerplate, script blocks, style blocks.
    Keeps: status code, headers, reflected input, error messages, dynamic text.
    Returns a compact snippet suitable for the hypothesis model.
    """
    text = response_text or ""

    # 1. Strip large HTML blocks
    text = HTML_STRIP_RE.sub(" [HTML_BLOCK_REMOVED] ", text)

    # 2. Strip remaining HTML tags
    text = TAG_STRIP_RE.sub(" ", text)

    # 3. Collapse whitespace
    text = WHITESPACE_RE.sub(" ", text)

    # 4. Redact session tokens (keep length info)
    text = SESSION_RE.sub(r"\1=[REDACTED]", text)

    # 5. Truncate to reasonable length for the hypothesis model
    text = text.strip()[:2000]

    # 6. Build header snapshot
    header_snap = ""
    if headers:
        important = {k.lower(): v for k, v in (headers if isinstance(headers, dict) else {}).items()
                     if k.lower() in ("server", "x-powered-by", "content-type", "content-length",
                                      "set-cookie", "www-authenticate", "x-frame-options",
                                      "x-content-type-options", "cf-ray", "x-cache")}
        header_snap = "\n".join(f"  {k}: {v}" for k, v in important.items())

    status_line = f"Status: {status_code}" if status_code is not None else ""

    parts = [p for p in [status_line, header_snap, text[:1500]] if p]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}

# CVSS 3.1 vectors for each anomaly class, used to attach a numeric score to
# every diagnosis instead of a bare low/medium/high label.
_SIGNAL_CVSS = {
    "backend_error": (7.5, "High", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"),
    "sql_error": (9.8, "Critical", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"),
    "command_injection": (9.8, "Critical", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"),
    "path_traversal": (7.5, "High", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"),
    "xss": (6.1, "Medium", "AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N"),
    "ssti": (8.8, "High", "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"),
    "waf_block": (0.0, "None", ""),
    "rate_limit": (0.0, "None", ""),
    "body_delta": (2.0, "Low", ""),
    "timeout": (2.0, "Low", ""),
    "reflection": (4.3, "Medium", "AV:N/AC:L/PR:N/UI:R/S:U/C:N/I:L/A:N"),
}


def _signal_cvss_class(signal: str) -> str:
    """Classify a signal string into a CVSS bucket."""
    if "backend_error" in signal or signal.startswith("HTTP_5"):
        return "backend_error"
    if "error_keywords" in signal:
        lowered = signal.lower()
        if any(k in lowered for k in ("sql", "mysql", "pg_query", "ora-", "syntax")):
            return "sql_error"
        if any(k in lowered for k in ("stack trace", "traceback", "exception", "fatal error", "call stack")):
            return "backend_error"
        if "mod_security" in lowered or "request denied" in lowered or "access denied" in lowered:
            return "waf_block"
    if "waf_or_rate_limit" in signal:
        return "rate_limit" if "429" in signal else "waf_block"
    if signal.startswith("body_length_delta"):
        return "body_delta"
    if signal.startswith("response_timeout"):
        return "timeout"
    if signal == "payload_reflection":
        return "reflection"
    return "backend_error"


def severity_to_cvss(severity: str) -> tuple[float, str]:
    """Map a legacy severity label to a numeric CVSS score + label."""
    table = {"low": (2.0, "Low"), "medium": (5.0, "Medium"), "high": (8.0, "High")}
    score, label = table.get(severity.lower(), (0.0, "None"))
    return score, label


def _bump_severity(current: str, new: str) -> str:
    """Raise severity only when new outranks current (fixes string-max bug)."""
    return new if _SEVERITY_RANK.get(new, 0) > _SEVERITY_RANK.get(current, 0) else current


def detect_anomaly(response_text, status_code=None, baseline_len=None, elapsed=None):
    """Check if a response is anomalous and needs diagnostic triage.

    Returns dict: {anomaly: bool, signals: [...], severity: "low"|"medium"|"high"}
    or {anomaly: False} if nothing unusual.
    """
    signals = []
    severity = "low"

    # Signal 1: HTTP 500 — backend crash
    if status_code is not None and 500 <= int(status_code) <= 599:
        signals.append(f"HTTP_{status_code}_backend_error")
        severity = _bump_severity(severity, "high")

    # Signal 2: HTTP 403 / 406 — likely WAF block
    if status_code in (403, 406, 429):
        signals.append(f"HTTP_{status_code}_waf_or_rate_limit")
        severity = _bump_severity(severity, "medium")

    # Signal 3: Response body length anomaly (>30% change from baseline)
    if baseline_len is not None and response_text:
        current_len = len(response_text)
        if baseline_len > 0:
            delta = abs(current_len - baseline_len) / baseline_len
            if delta > 0.30:
                signals.append(f"body_length_delta_{delta:.0%}")
                severity = _bump_severity(severity, "medium")

    # Signal 4: Timeout or excessive response time
    if elapsed is not None and elapsed > 15:
        signals.append(f"response_timeout_{elapsed:.0f}s")
        severity = _bump_severity(severity, "medium")

    # Signal 5: Error keywords in body
    error_keywords = [
        "sql syntax", "mysql_fetch", "pg_query", "ora-", "syntax error",
        "unclosed quotation", "stack trace", "exception", "debug mode",
        "warning: mysql", "fatal error", "call stack", "traceback",
        "mod_security", "request denied", "access denied",
    ]
    lowered = (response_text or "").lower()[:2000]
    matched_errors = [kw for kw in error_keywords if kw in lowered]
    if matched_errors:
        signals.append(f"error_keywords:{','.join(matched_errors[:3])}")
        severity = _bump_severity(severity, "high")

    # Signal 6: Reflection of payload (possible XSS or template injection)
    reflection_keywords = ["<script", "{{", "}}", "{%", "%}", "${", "onerror="]
    for kw in reflection_keywords:
        if kw in (response_text or ""):
            signals.append("payload_reflection")
            severity = _bump_severity(severity, "medium")
            break

    if not signals:
        return {"anomaly": False}

    # Attach a numeric CVSS score: take the most severe signal class.
    best_score, best_label = 0.0, "None"
    for sig in signals:
        cls = _signal_cvss_class(sig)
        score, label, _vec = _SIGNAL_CVSS.get(cls, (0.0, "None", ""))
        if score > best_score:
            best_score, best_label = score, label

    return {
        "anomaly": True,
        "signals": signals,
        "severity": severity,
        "cvss_score": best_score,
        "cvss_severity": best_label,
    }


# ---------------------------------------------------------------------------
# Hypothesis generation prompt — strict JSON schema
# ---------------------------------------------------------------------------
HYPOTHESIS_PROMPT = """# ROLE: ATTACK DIAGNOSTIC ANALYST
You analyze anomalous responses from a web application target and generate
THREE concrete, testable backend hypotheses. You NEVER guess — every hypothesis
must reference specific evidence in the provided diagnostic snippet.

# RULES
1. Output EXACTLY ONE JSON array with 3 hypothesis objects. NO other text.
2. Each hypothesis must be specific and falsifiable: "The server returned 500
   because the SQL payload caused a syntax error in a MySQL backend" — NOT
   "maybe a database error".
3. Include a concrete validation payload that would uniquely confirm THIS hypothesis
   while being DIFFERENT from the original payload that triggered the anomaly.
4. Include the specific expected response that would confirm the hypothesis
   (status code, substring that must appear, or substring that must NOT appear).

# JSON SCHEMA (MUST follow exactly):
[
  {
    "id": "H1",
    "hypothesis": "concrete causal theory (max 100 chars)",
    "confidence": 0.0-1.0,
    "validation_payload": "the payload to test this hypothesis",
    "validation_tool": "http_request or execute_terminal",
    "expected_confirm": "what must be TRUE if hypothesis is correct",
    "expected_disconfirm": "what must be TRUE if hypothesis is WRONG"
  },
  { "id": "H2", ... },
  { "id": "H3", ... }
]

# HYPOTHESIS CATEGORIES TO CONSIDER:
- WAF/IPS signature match: the string pattern tripped a rule
- Backend syntax error: malformed SQL/command/deserialisation
- Rate limiting / throttling: too many requests triggered a cooldown
- Input validation/sanitization: characters were stripped/escaped
- Application crash: recursive parsing or resource exhaustion
- Authentication/session rejection: token expired or IP blocked
- Encoding mismatch: the payload encoding caused truncation

# RESPONSE SNIPPET:
"""


def generate_hypotheses(diagnostic_snippet, original_payload="", config=None):
    """Feed the stripped diagnostic snippet to a fast model, get 3 hypotheses.

    Returns list of 3 hypothesis dicts or empty list on failure.
    """
    if _generate is None:
        console.print("[yellow][Oracle] No provider wired — falling back to heuristic.[/yellow]")
        return _heuristic_hypotheses(diagnostic_snippet, original_payload)

    user_msg = (
        f"{HYPOTHESIS_PROMPT}\n"
        f"{diagnostic_snippet}\n\n"
        f"Original payload that triggered anomaly: {original_payload or '(unknown)'}\n\n"
        f"Generate exactly 3 JSON hypotheses now."
    )

    messages = [
        {"role": "system", "content": "You are a diagnostic analyst. Output only valid JSON."},
        {"role": "user", "content": user_msg},
    ]

    try:
        # Use a cheap model for fast hypothesis generation
        supervisor_model = config.get("supervisor_model_id", config.get("sentinel_model_id")) if config else None
        resp = _generate(messages, config, model_id=supervisor_model,
                         temperature=0.1, max_tokens=600)
    except Exception as e:
        console.print(f"[yellow][Oracle] Hypothesis model failed: {e}[/yellow]")
        return _heuristic_hypotheses(diagnostic_snippet, original_payload)

    if isinstance(resp, str) and resp.startswith("Error:"):
        return _heuristic_hypotheses(diagnostic_snippet, original_payload)

    # Parse JSON
    try:
        m = re.search(r"\[[\s\S]*\]", resp)
        hypotheses = json.loads(m.group(0)) if m else json.loads(resp)
        if not isinstance(hypotheses, list):
            return _heuristic_hypotheses(diagnostic_snippet, original_payload)
        # Normalize and validate
        valid = []
        for i, h in enumerate(hypotheses[:3]):
            if not isinstance(h, dict):
                continue
            valid.append({
                "id": h.get("id", f"H{i+1}"),
                "hypothesis": h.get("hypothesis", "Unknown"),
                "confidence": float(h.get("confidence", 0.5)),
                "validation_payload": h.get("validation_payload", ""),
                "validation_tool": h.get("validation_tool", "http_request"),
                "expected_confirm": h.get("expected_confirm", ""),
                "expected_disconfirm": h.get("expected_disconfirm", ""),
            })
        return valid if valid else _heuristic_hypotheses(diagnostic_snippet, original_payload)
    except Exception:
        return _heuristic_hypotheses(diagnostic_snippet, original_payload)


def _heuristic_hypotheses(snippet, original_payload):
    """Deterministic fallback when the model is unavailable.

    Generates reasonable hypotheses without LLM — safe, cheap, always works.
    """
    hypotheses = []

    lowered = snippet.lower()
    payload = original_payload or ""

    # H1: WAF signature match
    if any(kw in lowered for kw in ("403", "406", "denied", "blocked", "mod_security", "waf")):
        hypotheses.append({
            "id": "H1",
            "hypothesis": "WAF/IPS signature match — the payload triggered a security rule",
            "confidence": 0.85,
            "validation_payload": _make_synonym_payload(payload),
            "validation_tool": "http_request",
            "expected_confirm": "Response changes from blocked to accepted (different status or body)",
            "expected_disconfirm": "Response remains identical — WAF might block entire parameter",
        })

    # H2: Backend syntax error
    if any(kw in lowered for kw in ("500", "syntax", "error", "exception", "traceback", "mysql", "sql")):
        hypotheses.append({
            "id": "H2",
            "hypothesis": "Backend syntax error — malformed SQL/command crashed the query parser",
            "confidence": 0.75,
            "validation_payload": _make_escaped_payload(payload),
            "validation_tool": "http_request",
            "expected_confirm": "500 disappears when payload is properly escaped/quoted",
            "expected_disconfirm": "500 persists regardless of quoting — not a syntax error",
        })

    # H3: Rate limiting
    if any(kw in lowered for kw in ("429", "timeout", "retry", "too many", "rate", "throttl")):
        hypotheses.append({
            "id": "H3",
            "hypothesis": "Rate limiting — too many requests triggered server throttling",
            "confidence": 0.80,
            "validation_payload": "[wait 10 seconds, retry original payload]",
            "validation_tool": "execute_terminal",
            "expected_confirm": "After waiting, the original payload succeeds again",
            "expected_disconfirm": "Response stays the same after waiting — not rate limiting",
        })

    # If we didn't have enough specific signals, add a generic H1
    if not hypotheses:
        hypotheses.append({
            "id": "H1",
            "hypothesis": "Input validation/sanitization — special characters stripped or escaped",
            "confidence": 0.60,
            "validation_payload": _make_synonym_payload(payload),
            "validation_tool": "http_request",
            "expected_confirm": "Synonym payload with different encoding bypasses the filter",
            "expected_disconfirm": "Response identical — filter might be on parameter name not value",
        })

    # Pad to 3 if needed
    while len(hypotheses) < 3:
        n = len(hypotheses) + 1
        hypotheses.append({
            "id": f"H{n}",
            "hypothesis": "Encoding mismatch — character encoding caused truncation or garbling",
            "confidence": 0.40,
            "validation_payload": _make_encoded_payload(payload),
            "validation_tool": "http_request",
            "expected_confirm": "URL-encoded version of payload produces different response",
            "expected_disconfirm": "Response identical — encoding is not the issue",
        })

    return hypotheses[:3]


def _make_synonym_payload(original):
    """Create a synonym payload: same intent, different syntax (for WAF bypass testing)."""
    if not original:
        return "' OR '1'='1"
    # Swap SQL syntax
    modified = original.replace("OR 1=1", "OR 2=2").replace("' OR '1'='1", "\" OR \"1\"=\"1\"")
    modified = modified.replace("--", "#").replace("' --", "'--")
    if modified == original:
        modified = original.replace("SELECT", "select").replace("UNION", "union")
    return modified


def _make_escaped_payload(original):
    """Create an escaped version (for backend syntax error testing)."""
    if not original:
        return "'\\'' OR 1=1 --"
    return original.replace("'", "\\'")


def _make_encoded_payload(original):
    """Create a URL-encoded version."""
    import urllib.parse
    if not original:
        return "%27%20OR%201%3D1%20--"
    return urllib.parse.quote(original)


# ---------------------------------------------------------------------------
# Binary verification
# ---------------------------------------------------------------------------
def verify_hypothesis(hypothesis, target_url, http_request_fn, execute_terminal_fn, config):
    """Execute the validation payload and confirm or disprove the hypothesis.

    This is the hard gate — NOTHING gets accepted as fact without a confirming
    tool call returning evidence.

    Args:
        hypothesis:       dict from generate_hypotheses()
        target_url:       base URL of the target application
        http_request_fn:  callable for HTTP requests (tools.http_request)
        execute_terminal_fn: callable for terminal commands (tools.execute_terminal)
        config:           app config

    Returns dict: {verified: bool, hypothesis_id: str, evidence: str, finding: str}
    """
    payload = hypothesis.get("validation_payload", "")
    tool = hypothesis.get("validation_tool", "http_request")
    hyp_id = hypothesis.get("id", "?")

    if not payload:
        return {
            "verified": False,
            "hypothesis_id": hyp_id,
            "evidence": "No validation payload provided — hypothesis cannot be tested.",
            "finding": "untestable",
        }

    # Rate-limit guard: wait before test request to avoid triggering rate limits
    time.sleep(2)

    try:
        if tool == "http_request":
            from urllib.parse import urljoin
            url = urljoin(target_url, "/") if target_url else "http://127.0.0.1:5000/"
            result = http_request_fn("POST", url, body=payload) if "=" in payload else http_request_fn("GET", url + "?" + payload)
        else:
            result = execute_terminal_fn(payload, timeout=15)
    except Exception as e:
        return {
            "verified": False,
            "hypothesis_id": hyp_id,
            "evidence": f"Verification call failed: {e}",
            "finding": "error",
        }

    # Analyze the result
    result_str = str(result)
    confirm_clue = hypothesis.get("expected_confirm", "")
    disconfirm_clue = hypothesis.get("expected_disconfirm", "")

    # Check confirmation and disconfirmation
    confirmed = _check_evidence(result_str, confirm_clue, disconfirm_clue, payload)

    return {
        "verified": confirmed,
        "hypothesis_id": hyp_id,
        "hypothesis": hypothesis.get("hypothesis", ""),
        "evidence": result_str[:500],
        "finding": "confirmed" if confirmed else "disproven",
    }


def _check_evidence(result, confirm_clue, disconfirm_clue, payload):
    """Heuristic evidence check — does the result support or refute the hypothesis?

    Uses substring matching + basic heuristics. The LLM-generated confirm/disconfirm
    strings are directional guides, not hard rules.
    """
    result_lower = result.lower()

    # Hard disconfirm: the payload literally didn't reach the target
    if "connection refused" in result_lower or "name resolution" in result_lower:
        return False

    # If confirm clue is very specific, check it
    if confirm_clue and len(confirm_clue) > 5 and confirm_clue.lower() in result_lower:
        return True

    # If disconfirm clue is present, strong signal against
    if disconfirm_clue and len(disconfirm_clue) > 5 and disconfirm_clue.lower() in result_lower:
        return False

    # Heuristic: did the status change?
    if "status: 2" in result_lower and "status: 5" not in result_lower:
        return True  # response normalized
    # Nothing conclusive — lean on the safe side: NOT verified.
    return "status: 4" not in result_lower and "status: 5" not in result_lower


# ---------------------------------------------------------------------------
# Top-level diagnostic pipeline
# ---------------------------------------------------------------------------
def diagnose(response_text, status_code, original_payload, target_url, config,
             http_request_fn, execute_terminal_fn):
    """Full diagnostic pipeline: detect → hypothesize → verify → record.

    Called from redteamer.py when a tool result looks anomalous.

    Returns: (verdict_str, knowledge_added)
      verdict_str:  human-readable diagnostic summary to inject into conversation
      knowledge_added: list of constraints added to the knowledge graph
    """
    kg = _get_kg()

    # Step 1: Detect anomaly
    anomaly = detect_anomaly(response_text, status_code=status_code)
    if not anomaly["anomaly"]:
        return None, []

    signals = anomaly.get("signals", [])
    severity = anomaly.get("severity", "low")

    console.print(
        f"[bold yellow][Oracle] Anomaly detected [{severity}]: {', '.join(signals[:4])}[/bold yellow]"
    )

    # Step 2: Strip response for token efficiency
    snippet = strip_response(response_text, status_code=status_code)
    console.print(f"[dim][Oracle] Diagnostic snippet: {len(snippet)} chars[/dim]")

    # Step 3: Generate hypotheses
    hypotheses = generate_hypotheses(snippet, original_payload, config)
    if not hypotheses:
        console.print("[red][Oracle] No hypotheses generated — skipping diagnostic.[/red]")
        return None, []

    console.print(f"[dim][Oracle] Generated {len(hypotheses)} hypotheses[/dim]")

    # Step 4: Verify hypotheses sequentially (binary gate)
    knowledge_added = []
    verdict_lines = [
        f"\n[ORACLE DIAGNOSIS • {severity.upper()} SEVERITY]",
        f"Signals: {', '.join(signals[:5])}",
        "",
    ]

    verified_any = False
    for h in hypotheses:
        console.print(f"[dim][Oracle] Testing {h['id']}: {h['hypothesis'][:80]}...[/dim]")
        result = verify_hypothesis(h, target_url, http_request_fn, execute_terminal_fn, config)

        if result["verified"]:
            verified_any = True
            # Record to knowledge graph
            constraint_type = _map_hypothesis_to_constraint(h["hypothesis"])
            kg.add_constraint(
                target=target_url or "unknown",
                constraint_type=constraint_type,
                rule=h.get("validation_payload", h["hypothesis"])[:200],
                evidence=result["evidence"][:300],
                confidence=0.95,
            )
            knowledge_added.append({
                "type": constraint_type,
                "rule": h["hypothesis"][:150],
                "id": h["id"],
            })

            verdict_lines.append(
                f"✅ {h['id']} CONFIRMED: {h['hypothesis']}\n"
                f"   Evidence: {result['evidence'][:200]}\n"
            )
            console.print(f"[green][Oracle] {h['id']} CONFIRMED ✓[/green]")

            # Record the finding as a constraint
            break  # Stop after first confirmed hypothesis
        else:
            verdict_lines.append(
                f"❌ {h['id']} DISPROVEN: {h['hypothesis']}\n"
            )
            console.print(f"[red][Oracle] {h['id']} disproven ✗[/red]")

    # Step 5: If all disproven, flag as false positive / unknown anomaly
    if not verified_any:
        verdict_lines.append(
            "\n⚠️  ALL HYPOTHESES DISPROVEN — unknown anomaly. "
            "Falling back to baseline methodology. Supervisor alerted."
        )
        kg.add_constraint(
            target=target_url or "unknown",
            constraint_type="false_positive",
            rule=f"anomaly_{'_'.join(signals[:3])}",
            evidence=snippet[:300],
            confidence=0.3,
        )
        console.print("[bold red][Oracle] All hypotheses disproven — false positive (?) flagged.[/bold red]")

    verdict_lines.append("\n[ORACLE END]")
    verdict_str = "\n".join(verdict_lines)

    return verdict_str, knowledge_added


def _map_hypothesis_to_constraint(hypothesis_text):
    """Map a hypothesis text to a knowledge graph constraint category."""
    lowered = hypothesis_text.lower()
    if any(kw in lowered for kw in ("waf", "signature", "mod_security", "blocked", "rule")):
        return "blocks"
    if any(kw in lowered for kw in ("syntax error", "database", "query", "malformed")):
        return "behavior"
    if any(kw in lowered for kw in ("rate", "throttl", "limit", "timeout")):
        return "rate_limit"
    if any(kw in lowered for kw in ("encoding", "truncat", "garbled")):
        return "behavior"
    if any(kw in lowered for kw in ("sanitiz", "filter", "strip", "escape", "validat")):
        return "blocks"
    if any(kw in lowered for kw in ("crash", "resource", "memory", "exhaustion")):
        return "behavior"
    return "behavior"

# ── Async wrapper for agent graph integration ─────────────────────────────────

async def generate_hypotheses_async(
    diagnostic_snippet: str,
    state: dict,
    generate_fn,
    original_payload: str = "",
) -> list:
    """Async wrapper — called from agent_graph._think()."""
    if generate_fn:
        try:
            prompt = HYPOTHESIS_PROMPT + "\n" + str(diagnostic_snippet)[:2000]
            response = await generate_fn(
                model_id=None,
                prompt=prompt,
                system="You are a diagnostic analyst. Output only valid JSON array.",
                max_tokens=600,
                temperature=0.1,
            )
            if response:
                import re as _re
                m = _re.search(r"\[[\s\S]*\]", str(response))
                if m:
                    import json as _json
                    hypotheses = _json.loads(m.group(0))
                    if isinstance(hypotheses, list) and len(hypotheses) > 0:
                        return hypotheses[:3]
        except Exception:
            pass
    return _heuristic_hypotheses(diagnostic_snippet, original_payload)
