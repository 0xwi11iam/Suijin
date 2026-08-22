# Verified Exploitation & Causal Reasoning Methodology

This document embeds the principle of VERIFICATION-FIRST exploitation. You are NOT allowed to accept anomalies at face value or generate hypotheses without binary testing. Every claim must be proven before it becomes actionable.

---

## 1. The Core Principle

**NEVER assume. ALWAYS verify.**

When a target returns something unusual — a 500 error, a 403, a timeout, a sudden change in body length — STOP the standard attack loop. Do NOT spray more payloads. The Oracle diagnostic system will analyze the response, generate hypotheses, and test them. Your job is to wait for verified findings before proceeding.

---

## 2. The Verification Pipeline

When an anomaly is detected:

```
Anomaly detected (500 / 403 / timeout / body length delta)
    
Oracle generates 3 concrete hypotheses (H1, H2, H3)
                        
  Test H1    Test H2    Test H3  (sequential, binary gate)
    
CONFIRMED -> Record to Knowledge Graph -> Use finding to adapt payload
    OR
ALL DISPROVEN -> Flag as unknown -> Alert supervisor -> Fallback to baseline
```

---

## 3. How to Use the Knowledge Graph

### BEFORE crafting any payload:

```json
{"tool": "check_knowledge", "args": {"target": "10.0.0.5"}}
```

This returns ALL known constraints for the target:
- Blocked patterns (e.g., "' OR 1=1" is blocked by WAF)
- Rate limits (e.g., 5 requests/minute)
- Verified CVEs that have been confirmed
- Working bypass strategies
- False positives that were previously flagged

### BEFORE testing a specific payload against a target:

```json
{"tool": "check_knowledge", "args": {"target": "10.0.0.5", "payload": "' OR 1=1 --"}}
```

If blocked: ` BLOCKED: Known block: 'OR 1=1' (verified 2024-06-24T...)` — modify your payload.
If clear: `[done] Payload not in any known blocked pattern` — safe to proceed.

### AFTER confirming a finding:

```json
{"tool": "record_finding", "args": {"target": "10.0.0.5", "finding_type": "verified_cve", "rule": "CVE-2021-41773", "evidence": "Path traversal confirmed — /etc/passwd extracted"}}
{"tool": "record_finding", "args": {"target": "10.0.0.5", "finding_type": "bypass", "rule": "URL-encode spaces as %20 bypasses WAF", "evidence": "200 response with encoded version vs 403 with raw spaces"}}
```

Finding types: `blocks`, `rate_limit`, `waf`, `verified_cve`, `false_positive`, `behavior`, `bypass`

---

## 4. Response Anomaly Types and What They Mean

| Signal | Likely Cause | Action |
|---|---|---|
| HTTP 500 | Backend crash (SQL syntax, deserialization, memory) | Diagnose with Oracle; if confirmed syntax error, adjust quoting/escaping |
| HTTP 403 / 406 | WAF/IPS signature match | Diagnose with Oracle; if confirmed WAF, try synonym payload or encoding bypass |
| HTTP 429 | Rate limiting | Wait 10-30 seconds before next request |
| Body length -30% or +30% | Filtering, reflection, or injection worked | Diff the response — is your payload reflected? Filtered? Rendered? |
| Timeout (15s+) | Backend hang, DNS resolution, or firewall drop | Reduce payload complexity, test baseline first |
| Error keywords (SQL syntax, traceback, etc.) | Backend is leaking error information | DIAGNOSE IMMEDIATELY — this is exploitable information |

---

## 5. The CVE Verification Standard

Before claiming a CVE is exploitable on a target:

1. **Fingerprint exact version** — Banner grab, HTTP headers, error pages
2. **Query NVD** — `search_cve(software, version=version)`
3. **Check knowledge graph** — `check_knowledge(target)` — was this CVE already verified?
4. **Attempt exploitation** — Run the payload or Metasploit module
5. **Verify result** — Did you get the expected outcome (file read, shell, auth bypass)?
6. **Record finding** — `record_finding(target, "verified_cve", "CVE-XXXX-YYYY", evidence)`

If exploitation FAILS but the version matches:
- Record as `behavior` with evidence: "CVE-2021-41773 test failed — target may have backported patch"
- Try the next CVE — do NOT loop on the same one

---

## 6. When the Oracle Diagnoses an Anomaly

If the Oracle injects a `[ORACLE DIAGNOSIS]` message into your conversation:

- **Read the verified findings** — the Oracle has already tested hypotheses
- **If H1 is CONFIRMED** — adapt your strategy based on the finding
- **If all disproven** — this is an unknown anomaly; switch to a DIFFERENT attack vector entirely
- **Never re-test a disproven hypothesis** — it wastes API credits

---

## 7. The Hallucination Prevention Rule

**Every claim about the target MUST have tool-call evidence backing it.**

- [fail] "The WAF is blocking `' OR 1=1`" — without testing a synonym payload
- [done] "The WAF is blocking `' OR 1=1` — confirmed: `\" OR 1=1 --` bypassed with 200, recorded to knowledge graph"

If you find yourself about to state a fact about the target that wasn't returned by a tool call, STOP. Run a verification tool call first.
