# Security Policy

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Report vulnerabilities privately to the maintainers:

- **Email**: [jiangwilliam30@gmail.com] (primary)
- **GitHub**: Use the "Report a vulnerability" button under the Security tab -> "Private vulnerability reporting"

You can expect:

| Timeline | Action |
|---|---|
| Within 48h | Acknowledgement of receipt |
| Within 7 days | Initial triage and severity assessment |
| Within 30 days | Fix released (sooner for critical issues) |

### What to include in a report

1. Affected version (e.g., v2.0.1)
2. Type of issue (e.g., command injection, path traversal, unsafe deserialization)
3. Reproduction steps or a minimal PoC
4. Impact assessment (what an attacker could achieve)


## Security Model

Suijin is a **dual-use offensive/defensive security tool**. It is intended for authorized testing only (see [DISCLAIMER](README.md#legal-disclaimer)). The security model assumes:

- The operator has **explicit authorization** for the target
- The host running Suijin is **trusted and single-operator**
- API keys in `suijin/.env` are **never committed** (gitignored)

### Known accepted risks (by design)

| Risk | Rationale | Mitigation |
|---|---|---|
| AI can execute shell commands | Core feature — the agent drives `nmap`, `sqlmap`, etc. | `guardrails.py` blocks destructive patterns (`rm -rf /`, `mkfs`, fork bombs); self-kill protection refuses to kill its own PID |
| Blue team AI can patch code | Core feature — autonomous hotfix | Patches logged to the knowledge graph + audit trail; hotfix is opt-in per config |
| Workspace file writes | Agent needs a scratch area | `workspace.py` confines writes to `suijin_agent/` + `/tmp` allowlist; symlink-resolved boundary checks |
| LLM prompt injection via target content | Tool output enters prompts | `prompt_safety.py` wraps untrusted content with unforgeable boundary markers |
| Gov/mil/edu targets | Not allowed | `hard_guardrail.py` blocks these domains |

### Reporting a vulnerability IN the tool

If you discover a vulnerability in a component Suijin depends on (e.g., Flask lab dependency), report it to that project upstream and optionally notify us so we can bump the dependency.

## Security CI

Every push runs:

- `pip-audit` — known CVE scan on Python dependencies
- `ruff` — static analysis
- pytest — 286 behavioral + integration tests including guardrail verification
- Coverage gate (30% floor)
