<p align="center">
  <img src="assets/medusa.png" alt="Medusa Logo" width="160"/>
</p>
<h1 align="center">Medusa</h1>

<p align="center">
  <img height="20" src="https://img.shields.io/badge/v2.7.0-operators__cli-green?style=flat" alt="Version"/>
  <img height="20" src="https://img.shields.io/badge/LICENSE-MIT-4169A1?style=flat" alt="License"/>
  <img height="20" src="https://img.shields.io/badge/PYTHON-3.10+-306998?style=flat&logo=python&logoColor=white" alt="Python"/>
  <img height="20" src="https://img.shields.io/badge/LangGraph-State%20Machine-FF6B35?style=flat" alt="LangGraph"/>
</p>

Medusa is a dual-mode autonomous security platform: a **Red Team** agent that
chains reconnaissance → exploitation → reporting over a LangGraph state machine,
and a **Blue Team** agent that monitors live HTTP traffic, detects attacks, and
responds with deception, blocking, and source patching. Both modes share one
toolkit, one knowledge base, and one knowledge graph.

> **LEGAL DISCLAIMER**: This tool is intended for **authorized security
> testing**, **educational purposes**, and **research only**. Never use this
> system to scan, probe, or attack any system you do not own or have explicit
> written permission to test. Unauthorized access is **illegal**. By using this
> tool you accept full responsibility for your actions.

---

## Table of Contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [CLI Reference](#cli-reference)
4. [First Engagement](#first-engagement)
5. [Configuration](#configuration)
6. [LLM Providers](#llm-providers)
7. [Knowledge Base](#knowledge-base)
8. [Agent Workspace](#agent-workspace)
9. [Architecture](#architecture)
10. [Red Team Reference](#red-team-reference)
11. [Blue Team Reference](#blue-team-reference)
12. [Built-in Labs](#built-in-labs)
13. [Testing](#testing)
14. [Project Layout](#project-layout)
15. [Troubleshooting](#troubleshooting)
16. [Glossary](#glossary)
17. [Contributing & Credits](#contributing--credits)

---

## Requirements

| Requirement | Details |
|:------------|:--------|
| Python | 3.10+ (3.14 tested) |
| OS | macOS, Linux, Windows |
| LLM API key | Optional — heuristic mode works without one |

---

## Installation

### One command (macOS / Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/0xwi11iam/Medusa/main/install.sh | bash
medusa doctor     # verify the environment
medusa selftest   # offline smoke test (no network, no API keys)
medusa            # launch the interface
```

The installer clones into `~/.medusa/repo`, creates an isolated virtualenv, and
drops a `medusa` launcher on your PATH. Environment overrides:
`MEDUSA_INSTALL_DIR`, `MEDUSA_BIN_DIR`, `MEDUSA_REPO`, `MEDUSA_NO_PATH_EDIT`.

### Manual

```bash
git clone https://github.com/0xwi11iam/Medusa.git && cd Medusa
python3 -m venv .venv && source .venv/bin/activate
pip install -r medusa/requirements.txt
python3 medusa/main.py
```

### Docker

```bash
docker build -t medusa .
docker run -it --rm \
  -v $(pwd)/medusa_agent:/app/medusa_agent \
  -e ZAI_API_KEY=$ZAI_API_KEY \
  medusa
```

---

## CLI Reference

`medusa` bare launches the Rich TUI. Every subcommand below is
**non-interactive, offline, and scriptable** (exit 0 = healthy).

| Command | What it does |
|:--------|:-------------|
| `medusa` | Launch the classic Rich TUI (Red / Blue / Settings) |
| `medusa doctor` | Full environment check: python, deps, binaries, config, modules, KB, workspace |
| `medusa selftest` | Offline smoke test: imports, KB gating, workspace anchors, sandbox, boundaries |
| `medusa status` | One-page summary: provider, KB, workspace, modules, lab port |
| `medusa version` | Release, codename, python, platform, package path |
| `medusa env` | API key presence by name — values are never printed |
| `medusa tools` | All 112+ agent tools with availability (missing binaries marked) |
| `medusa modules` | Loaded module packs with tool counts and dependencies |
| `medusa skills` | Agent-editable attack/defense skills |
| `medusa config show` | Effective config (defaults merged), secrets redacted |
| `medusa config validate` | Pydantic validation of `config.json` + `blue_config.json` (exit 1 on failure) |
| `medusa workspace` | Workspace layout, per-directory usage, symlink health |
| `medusa reports` | Engagement reports in `medusa_agent/reports/` (newest first) |
| `medusa sessions` | Saved engagement sessions with objectives |
| `medusa labs` | Built-in vulnerable labs: ports, descriptions, launch commands |
| `medusa pull kb` | Download + index the knowledge base (**enables** KB features) |
| `medusa pull kb --status` | Offline: what's indexed, per-source counts, build age |
| `medusa pull kb --list` | Available sources with size warnings |
| `medusa pull kb --sources <names>` | Pull a subset (rebuilds the DB with just those) |
| `medusa pull kb --force` | Re-download even if tarballs are cached |

Examples:

```bash
medusa status && medusa labs
medusa pull kb --sources hacktricks gtfobins   # skip the 300 MB SecLists pull
medusa config validate || echo "fix config.json"
```

---

## First Engagement

### Red Team

```bash
# Terminal 1: start a lab
python3 medusa/lab/blue_target/vulnerable_app.py        # :5906

# Terminal 2: launch and point the agent at it
python3 medusa/main.py   # choose [1] Red Team, target http://127.0.0.1:5906
```

The agent runs the chain autonomously — port scan, endpoint discovery,
directory brute-force, CVE lookup, exploitation — logging every step to the
audit trail and `.notes/`, and finishes with a report in
`medusa_agent/reports/`.

### Blue Team

```bash
# Terminal 1: Blue Team starts and watches the built-in lab
python3 medusa/main.py   # choose [2] Blue Team → 2 (built-in lab :5906)

# Terminal 2: attack it once the baseline locks (after 25 requests)
python3 medusa/lab/blue_target/attack_simulator.py
# or by hand:
curl -X POST http://127.0.0.1:5906/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin'"'"' OR '"'"'1'"'"'='"'"'1","password":"x"}'
```

### Purple teaming

Run both at once: Blue defends the lab while Red attacks it. The knowledge
graph is shared, so every flag claimed and every defense deployed is visible
to both sides.

---

## Configuration

Configuration lives in **`medusa/config.json`** (red team) and
**`medusa/blue_config.json`** (blue team). API keys live in `medusa/.env` or
environment variables — **never in config.json**. Validate with
`medusa config validate`; inspect with `medusa config show` (secrets redacted).

### `medusa/config.json` — key reference

| Key | Default | Meaning |
|:----|:--------|:--------|
| `provider` | `"deepseek"` | LLM provider id (see [Providers](#llm-providers)) |
| `deepseek_model` | `"deepseek-v4-flash"` | DeepSeek model |
| `zai_model` | `"glm-5.3"` | Z.ai GLM model |
| `zai_endpoint` | `"coding"` | Z.ai billing surface: `"coding"` (Coding Plan) or `"paas"` (pay-as-you-go) |
| `gemini_model` | `"gemini-2.5-flash"` | Gemini model |
| `anthropic_model` | `"claude-opus-4-7"` | Anthropic model |
| `temperature` | `0.4` | Sampling temperature (0.0–2.0) |
| `max_tokens_per_request` | `8000` | Per-call token ceiling |
| `max_iterations` | `100` | Agent loop cap |
| `supervisor_interval` | `5` | Supervisor runs every N iterations |
| `supervisor_model_id` | `"Qwen/Qwen2.5-3B-Instruct"` | Supervisor model (HF) |
| `cost_alert_usd` / `cost_budget_usd` / `cost_hard_cap_usd` | `0.25` / `1.0` / `2.0` | Cost guardrails |
| `mode_hitl` | `false` | Human-in-the-loop: blocks non-recon tools until approved |
| `mode_guardrail` | `false` | Blocks destructive shell commands (rm/mv/chmod/kill) |
| `mode_deploy_subagent` | `true` | Allow parallel subagents |
| `mode_audit_trail` | `true` | Zero-truncation JSON/MD audit logging |
| `mode_hotreload_skills` | `true` | Skills reload on edit |
| `subagent_count` | `2` | Max parallel subagents (1–5) |
| `report_auto_export` / `report_format` | `true` / `"markdown"` | Report generation |
| `proxy_url` | — | Outbound proxy for all tool HTTP traffic |
| `metasploit_rpc_host` / `_port` / `_ssl` | `127.0.0.1` / `55553` / `false` | msfrpcd connection |
| `agent_workspace` | `"medusa_agent"` | Workspace folder name (see [Workspace](#agent-workspace)) |

Unknown keys are caught at startup by Pydantic validation; `zai_endpoint`
accepts only `coding`, `paas`, or a full custom base URL.

### `medusa/blue_config.json` — key reference

```json
{
    "traffic_normalization_turns": 25,
    "scorer":       {"critical_threshold": 8, "suspicious_threshold": 5},
    "watchers":     {"max_per_endpoint": 3, "health_check_interval": 30},
    "deception":    {"auto_honeypot": true, "auto_tarpit": true,
                     "tarpit_delay_seconds": 8, "shadow_redirect_threshold": 8},
    "response":     {"auto_block_critical": true, "max_blocks_per_hour": 50},
    "hotfix":       {"auto_patch_critical": false, "silent_patch_mode": true},
    "cost":         {"daily_budget_usd": 5.00, "max_llm_calls_per_minute": 20}
}
```

---

## LLM Providers

| Provider | Models | Env var |
|:---------|:-------|:--------|
| **Z.ai (GLM)** | `glm-5.3` (default), `glm-5-turbo`, `glm-4.7` | `ZAI_API_KEY` |
| DeepSeek | `deepseek-v4-flash`, `deepseek-v4-pro` | `DEEPSEEK_API_KEY` |
| HuggingFace | Qwen, GLM, DeepSeek via TGI | `HF_TOKEN` |
| Gemini | `gemini-2.5-pro`, `gemini-2.5-flash` | `GEMINI_API_KEY` |
| Anthropic | `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5` | `ANTHROPIC_API_KEY` |
| AMD | via `amd_config.endpoint` | `AMD_API_KEY` |

`NVD_API_KEY` (optional) raises NVD rate limits for the `search_cve` tool.

### Z.ai: Coding Plan vs pay-as-you-go

Z.ai serves **two separate chat-completions endpoints** that accept the same
`ZAI_API_KEY` but bill differently. Pick with `zai_endpoint` in
`medusa/config.json` (Settings TUI → provider `zai` → `zai_endpoint`, or
`medusa config validate` catches typos):

| `zai_endpoint` | Base URL | Billing |
|:---------------|:---------|:--------|
| `"coding"` **(default)** | `https://api.z.ai/api/coding/paas/v4` | GLM Coding Plan subscription (Lite/Pro/Max) — burns plan **credits**, never dollars. Models: `glm-5.3`, `glm-5-turbo`, `glm-4.7` (older GLM ids auto-route to glm-5.3). |
| `"paas"` | `https://api.z.ai/api/paas/v4` | Pay-as-you-go — per-token **USD** billing, full GLM catalogue. Choose this only if you don't have a Coding Plan. |

A Coding Plan key hitting the `paas` endpoint (or vice versa) returns **403** —
Medusa detects this and prints the exact fix instead of retrying. `medusa
doctor` and `medusa status` show the active endpoint. A full custom base URL
(e.g. a proxy) is also accepted as `zai_endpoint`.

Docs: <https://docs.z.ai/devpack/tool/others>

---

## Knowledge Base

`medusa pull kb` **downloads and indexes** the offline security knowledge base
into one SQLite FTS5 database — that act **enables** all KB features. Until you
run it, they stay **disabled** (`search_kb` reports DISABLED, the tool catalog
lists it under a disabled section, and the agent asks the operator to run the
pull).

```bash
medusa pull kb              # download all sources and compile to SQLite FTS5
medusa pull kb --status     # what's indexed, per-source counts, build age
medusa pull kb --list       # available sources (incl. size warnings)
medusa pull kb --sources hacktricks gtfobins   # subset (replaces the DB)
medusa pull kb --force      # ignore cached tarballs
```

| | |
|:--|:--|
| **Sources** | HackTricks, PayloadsAllTheThings, GTFOBins (`GTFOBins.github.io` — path-pattern matched under `_gtfobins/`, alias stubs like `awk -> mawk` resolved), LOLBAS, OWASP Cheat Sheets, SecLists (`~300 MB`, warned before download) |
| **Storage** | `medusa/kb.sqlite3` (FTS5, BM25-ranked) + `medusa/kb_cache/` tarballs — always inside the repo, never bundled |
| **Agent tool** | `search_kb` — ranked results with source + snippet, offline. Optional `source:<name>` filter (e.g. `"source:gtfobins awk sudo"`) and `limit` 1–20 (default 5) |
| **Honest status** | Only sources that actually indexed docs are counted; a source that downloads but matches 0 files is a **failure** with a pattern hint, never a silent gap |
| **Resilient pulls** | 3 download attempts per ref with backoff, stale `.part` files discarded (never resumed), progress logging every 50 MB, 600 s timeout |

The agent's attack rhythm is KB-first: *fingerprint → search_kb → search_cve →
attack*. One dead source never kills a pull — failures are skipped, reported,
and retryable with `--sources <name>`. `medusa doctor` shows per-source doc
counts and a STALE warning when the build is older than 30 days.

### Agent toolkit built on the KB

Beyond `search_kb`, the agent gets seven offline tools (all work without any
API key; the four marked ★ need the KB built):

| Tool | What it does |
|:-----|:-------------|
| ★ `suggest_exploit` | Fingerprinted service → exact GTFOBins privesc page + HackTricks + PayloadsAllTheThings leads, offline |
| ★ `find_wordlist` | Keyword → matching SecLists wordlists, **materialized** into `medusa_agent/wordlists/` ready for `ffuf -w` |
| ★ `extract_payloads` | Pulls runnable code blocks from KB docs into `medusa_agent/payloads/` |
| ★ `kb_stats` | Per-source inventory, build age, failed sources |
| `wordlist_tool` | Merge / dedupe / length-filter wordlists |
| `mine_failures` | Clusters `failure_db.json` into technique/reason patterns to stop repeating |
| `anonymize_report` | Scrubs IPs/emails/tokens/JWTs/keys from a report before sharing (localhost + `FLAG{}` preserved) |

`search_kb` also supports **phrase queries**: quoted spans match adjacent,
in-order words — `"union select"` won't match `select ... union`.

---

## Agent Workspace

All agent artifacts live in **one** root-level `medusa_agent/`:

```
medusa_agent/
├── reports/         engagement reports (markdown/html/json)
├── audit_trails/    zero-truncation JSON/MD audit logs
├── sessions/        saved sessions for replay
├── blue_state/      blue-team session state
├── dossiers/        attacker profiles
├── outputs/         background-job logs + offloaded tool output
├── payloads/ ── scripts/ ── sandbox/
├── evidence/ ── evidence_chains/ ── goals/
├── credentials.json discovered credentials
└── SOUL.md          agent persona file
```

The layout is **self-repairing**: on startup, `ensure_workspace_layout()`
(`medusa/tools/workspace.py`) merges any legacy real `medusa/medusa_agent/`
directory up into the root workspace and replaces the inner path with a
symlink `-> ../medusa_agent`. All writes go through one anchor
(`WORKSPACE_DIR`); absolute paths outside the workspace and `/tmp` allowlist
are rejected; the shell sandbox lives at `medusa_agent/sandbox`. KB artifacts
stay strictly in `medusa/` — never inside the workspace.

Check it: `medusa workspace` (usage + symlink health), `medusa selftest`
(boundary + sandbox containment invariants).

---

## Architecture

```mermaid
graph TB
    subgraph "Medusa Core"
        MAIN[main.py<br/>Mode Selector]
        RED[redteamer.py<br/>LangGraph State Machine]
        BLUE[blueteamer.py<br/>Live Traffic Monitor]
        THINK[think_node.py<br/>ReAct + 7 Action Types]
        TOOLS[dispatch.py<br/>112+ Tools]
        SUP[supervisor.py<br/>Pattern Detector]
    end
    subgraph "Red Team"
        NMAP[nmap] & SQLMAP[sqlmap] & GOBUSTER[gobuster]
        META[metasploit] & HYDRA[hydra] & NUCLEI[nuclei]
        MORE[...]
    end
    subgraph "Blue Team"
        FEED[LiveFeed<br/>18 Attack Detectors]
        AI[BlueAIEngine<br/>LLM Decisions]
        KG2[Knowledge Graph<br/>Shared Intel]
        DECEIVE[Tarpit + Honeypot<br/>pfctl Blocking]
        SUB[Per-Endpoint<br/>AI Subagents]
    end
    MAIN --> RED & BLUE
    RED --> THINK --> TOOLS
    BLUE --> FEED --> AI --> DECEIVE
    FEED --> KG2 --> SUB --> AI
    TOOLS --> NMAP & SQLMAP & GOBUSTER & META & HYDRA & NUCLEI & MORE
    SUP -.->|every 5 iters| RED
```

Dual-mode summary:

| | Red Team | Blue Team |
|:--|:--|:--|
| **Goal** | Discover, verify, exploit vulnerabilities; claim flags; produce a report. | Detect, deceive, block, and patch attackers; maintain defense logs and attacker profiles. |
| **Driver** | LangGraph state machine + supervisor + parallel subagents. | 18 pre-AI detectors + per-endpoint AI subagents + response ladder. |
| **Tools** | nmap, gobuster, feroxbuster, amass, sqlmap, hydra, Metasploit, john, CVE/KB search. | Tarpit, network block, canary tokens, patch engine, KG profiling. |
| **Output** | Findings, flags, exploit chains, audit trail, attack tree. | Incident feed, defense log, attacker history, patches applied. |

---

## Red Team Reference

### Pipeline

`recon → vuln discovery → exploit → escalate → flag → report`, driven by the
think node (ReAct) over a LangGraph state machine. Every step's tool call and
raw output is persisted to the audit trail.

### Supervisor — zero-cost oversight

Runs silently every 5 iterations (configurable). Pure pattern matching — no
LLM calls, zero API cost.

| Pattern | Trigger | Intervention |
|:--------|:--------|:-------------|
| Loop | Same tool 3x consecutively | "Try a DIFFERENT approach. Switch tool or attack vector." |
| Bookkeeping Trap | 4+ turns of notes/jobs | "STOP documenting. START exploiting NOW." |
| Missed Flag | `FLAG{...}` found but not claimed | "Claim it IMMEDIATELY with claim_flag." |
| Unfollowed Vuln | Vuln discovered, no follow-up | "Test the vulnerability NOW. Don't pivot." |
| Failing Subagents | 3+ subagents returned empty | "Subagents keep failing. Run the task yourself." |
| Stall | 5 turns with no new info | "Radically change approach or generate report." |

### Subagents

```json
{"action": "deploy_subagent",
 "subagent_task": "SQLi on /login || XSS on /search || SSTI on /profile",
 "thought": "Parallelizing attack vectors across all endpoints"}
```

| Property | Value |
|:---------|:------|
| Max concurrent | 3 (semaphore) |
| Max steps | 5 per subagent |
| LLM timeout | 45 s |
| Tool timeout | 60 s |
| Total timeout | 95 s |
| Crash isolation | Yes — one failure doesn't kill others |

### Runtime controls

| Command | Context | Action |
|:--------|:--------|:-------|
| `Ctrl+C` | During run | Pause agent, enter guidance mode |
| `/report` | Paused | Force-generate report + end audit |
| `/audit` | Paused | Print current audit trail |
| `/state` | Paused | Print agent state (phase, iterations, cost) |
| `/sessions` | Paused | List saved sessions for replay |

---

## Blue Team Reference

### Request processing — three tiers

| Tier | Trigger | AI Cost | Response |
|:-----|:--------|:--------|:---------|
| **NORMAL** | Matches known-safe baseline | $0.00 | Logged for audit only |
| **ANOMALOUS** | Deviates from baseline, no attack pattern | ~$0.001 | AI classifies → baseline or INVESTIGATED |
| **INVESTIGATED** | Attack pattern detected or AI flagged | ~$0.002 | AI decides: BLOCK / DECEIVE / PATCH / LOG / REDIRECT |

**Baseline learning:** the first 25 requests build pattern profiles
(`SmartNormalizer` hashes by method, normalized path, param keys, body
structure). After 25 requests the baseline locks and AI analysis activates.

### Pre-AI pattern detector — 18 signatures

| # | Pattern | Weight | Example |
|:-:|:---------|:--:|:--------|
| 1 | SQL Injection | 5 | `admin' OR '1'='1`, `UNION SELECT` |
| 2 | SQL Injection (Blind) | 5 | `' OR SLEEP(5)`, `BENCHMARK()` |
| 3 | XSS | 5 | `<script>`, `onerror=`, `javascript:` |
| 4 | Path Traversal | 4 | `../../etc/passwd` |
| 5 | SSRF | 5 | `169.254.169.254`, `metadata.google.internal` |
| 6 | Command Injection | 5 | `; id`, `` `whoami` ``, `$(...)` |
| 7 | SSTI | 4 | `{{7*7}}`, `${7*7}` |
| 8 | XXE | 5 | `<!ENTITY xxe SYSTEM` |
| 9 | JWT Attack | 3 | alg:none, key confusion |
| 10 | Deserialization | 5 | `pickle.loads`, `yaml.load(` |
| 11 | LDAP Injection | 4 | `(&(uid=*)(|` |
| 12 | NoSQL Injection | 4 | `{"$ne": null}` |
| 13 | Scanner User-Agent | 4 | sqlmap, Nikto, Burp |
| 14 | Mass Assignment | 4 | `"role":"admin"` |
| 15 | Auth Bypass Header | 5 | `X-Admin: true` |
| 16 | Brute Force | 3 | Hydra/Medusa UA, repeated attempts |
| 17 | File Inclusion | 5 | `php://filter`, `data://text` |
| 18 | GraphQL Attack | 3 | `__schema`, deep nesting |

Score threshold: **5**. Repeat offenders gain +1 effective score per flag —
a persistent attacker escalates from DECEIVE to BLOCK automatically. The
pattern detector has veto power even when the AI classifies benign.

### AI decision engine

For every anomalous/investigated request the engine builds a prompt with the
full request, attacker history from the knowledge graph, the endpoint's
handler source code, and subagent intelligence, and receives a structured
decision:

```json
{
  "verdict": "FLAGGED",
  "score": 9,
  "action": "DECEIVE",
  "attack_analysis": "SQL injection in username field using OR 1=1 bypass",
  "attacker_assessment": "Automated scanner, low skill",
  "reasoning": "Classic SQLi. Endpoint uses raw string concatenation.",
  "commands_to_run": ["echo '{\"127.0.0.1\":{\"delay\":5}}' > /tmp/blue_tarpit.json"],
  "code_changes": [
    {"file": "vulnerable_app.py", "change": "Parameterize SQL query",
     "new_content": "conn.execute('SELECT * FROM users WHERE username=?', (username,))"}
  ]
}
```

Commands are executed, code changes written to the target filesystem, and
everything recorded in the knowledge graph. Escalation doctrine: novel
attacker → DECEIVE (gather intelligence); repeat offender → BLOCK; critical
vuln (RCE/data exfil) → BLOCK + PATCH; benign anomaly → LOG and baseline. If
the AI is unreachable (bad key, quota, network), pattern-based fallback
defense still deploys — defense never depends on the AI being available.

### Deception arsenal

- **Tarpit** — flagged IPs get real `time.sleep(delay)` per request via
  `/tmp/blue_tarpit.json` (measured: 0.018 s normal → 5.8 s at score 6 →
  8.0 s at score 10). Expires after 30 minutes.
- **Network blocking** — score 8+ triggers `pfctl -t blue_blocked -T add <IP>`
  (iptables fallback on Linux).
- **Honeypots** — decoy admin users with canary API keys, phantom debug
  tokens, decoy config endpoints.
- **Codebase patching** — full-file rewrites of vulnerable handlers;
  silent-patch mode keeps the original endpoint as a trap.

### Session knowledge graph

`/tmp/blue_kg.json` — shared across subagents and the main engine:
**attacker** nodes (IP, first seen, flags, tools), **attack** nodes (path,
type, score, payload), **defense** nodes (tarpit/block/patch + detail), and
**intelligence** nodes (subagent findings). `get_attacker_history(ip)` feeds
the AI full context so responses escalate with repetition.

### Runtime commands

| Command | Action |
|:--------|:-------|
| `Ctrl+C` | Pause feed, enter command mode |
| `/state` | Endpoints, subagents, requests, baseline status, AI cost |
| `/report` | KG summary: top attackers, attack/defense counts |
| `/health` | System health check |
| `/quit` | End session, save state |

---

## Built-in Labs

Eight deliberately vulnerable Flask apps ship in `medusa/lab/` — practice
without touching anything you don't own. `medusa labs` lists them live with
ports and launch commands.

| Lab | Port | Launch | Focus |
|:----|:-----|:-------|:------|
| cloud_iam_lab | 5900 | `python3 medusa/lab/cloud_iam_lab/app.py` | AWS IAM misconfigurations |
| api_only_lab | 5901 | `python3 medusa/lab/api_only_lab/app.py` | REST + GraphQL: BOLA, mass assignment, rate-limit bypass |
| oauth_lab | 5902 | `python3 medusa/lab/oauth_lab/app.py` | OAuth 2.0 / OIDC misconfigurations |
| log4shell_lab | 5903 | `python3 medusa/lab/log4shell_lab/app.py` | Log4j RCE |
| wordpress_lab | 5904 | `python3 medusa/lab/wordpress_lab/app.py` | WordPress + vulnerable plugins |
| ad_lab | 5905 | `python3 medusa/lab/ad_lab/app.py` | Simulated AD DC: Kerberos, LDAP, SMB |
| blue_target | 5906 | `python3 medusa/lab/blue_target/vulnerable_app.py` | 25 endpoints, 8 route groups, 15+ vuln classes (below) |
| devops_dashboard | 5700 | `python3 medusa/lab/devops_dashboard/app.py` | Hard RCE lab — multi-step chain required |

### blue_target (:5906) — route groups

| Group | Endpoints | Vulnerabilities |
|:------|:----------|:----------------|
| Auth | `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/me`, `/auth/reset-password` | SQLi login, mass assignment (role=admin), JWT alg:none, predictable reset tokens |
| Users API | `/api/users`, `/api/users/<id>` | IDOR, no CSRF on delete |
| Search | `/api/search` | SQLi in field name AND value |
| Documents | `/api/documents/<id>/download` | IDOR, path traversal, upload extension bypass |
| Export | `/api/export` | XXE file read |
| Templates | `/api/templates/<name>` | SSTI via `eval()` |
| Execute | `/api/execute` | Command injection (`shell=True`) |
| Coupons | `/api/coupons/redeem` | Race condition (0.5 s window) |
| GraphQL | `/graphql` | Introspection enabled, no depth limit |
| Admin | `/admin`, `/admin/config` | `X-Admin: true` bypass, SSRF webhook |
| Health/Debug | `/health`, `/debug/state` | Info disclosure |
| Landing | `/` | Full endpoint enumeration |

Example attack chain: register as admin (mass assignment) → admin JWT → IDOR
user dump → UNION-inject search → path-traversal file read → RCE via
`/api/execute`.

---

## Testing

```bash
python3 -m pytest medusa/tests/ -q          # full suite (offline)
python3 -m pytest medusa/tests/ -m "not ai" # skip live-API tests
```

500+ tests across 16 files — all offline (network mocked, no API keys needed).

| Test file | Covers |
|:----------|:-------|
| `test_cli_commands.py` | All non-interactive CLI verbs: status/version/env/tools/modules/skills/labs/workspace, config show redaction + validate, reports/sessions listings, doctor workspace row |
| `test_zai_provider.py` | Z.ai dual endpoints (coding default / paas / custom URL / 403 guidance), model remapping, retries, pricing, config validation, doctor row |
| `test_kb_tools.py` | find_wordlist (search + tarball extraction), kb_stats, suggest_exploit (GTFOBins alias resolution), extract_payloads, wordlist_tool merge/filter, mine_failures clustering, anonymize_report scrubbing, search_kb phrase queries |
| `test_kb.py` | KB compile (FTS5, caps), path patterns + GTFOBins alias stubs, zero-doc failures, honest status, download retries + `.part` cleanup, `search_kb` filters, catalog gating |
| `test_workspace_layout.py` | Canonical workspace merge + symlink migration, sandbox containment, CWD-independent paths |
| `test_dispatch.py` | Tool routing, guardrails, file ops, CVSS/KEV parsing, jobs |
| `test_state_helpers.py` | State models, parsing, productivity, guardrails, provider routing |
| `test_blue_team.py` | AI engine, feed, scorer, deception, firewall, SOC, tarpit |
| `test_e2e_blue.py` | Live-lab integration: real SQLi → detection → tarpit delay |
| `test_graph.py`, `test_integration.py`, `test_core.py`, `test_tools.py`, `test_agent_helpers.py`, `test_ai_calls.py` | State machine, pipelines, guardrails, workspace FS, config loading |

**CI:** GitHub Actions matrix (Python 3.10/3.11/3.12) — pytest + coverage,
pyright, ruff, pip-audit.

---

## Project Layout

```
medusa-security/
├── medusa/                  Python package (the whole backend)
│   ├── cli.py               CLI entry — doctor, selftest, status, pull kb, ...
│   ├── main.py              Rich TUI launcher
│   ├── kb.py                Knowledge base: download, index, FTS5 compile
│   ├── core/                Red + blue engines, config models, state
│   │   ├── redteamer.py     LangGraph red-team driver
│   │   ├── blueteamer.py    Blue-team driver
│   │   └── blue/            Detectors, deception, SOC, subagents, TUI feed
│   ├── tools/               dispatch.py hub + tool modules
│   │   ├── providers.py     LLM providers (Z.ai coding/paas, DeepSeek, ...)
│   │   └── workspace.py     Canonical workspace anchor + layout repair
│   ├── infra/               Job runner, output offload, workspace FS
│   ├── modules/             Module-pack loader
│   ├── prompts/             System prompts + tool registry
│   ├── skills/              Agent-editable skill files
│   ├── nodes/               LangGraph nodes (think, execute, initialize)
│   ├── lab/                 8 deliberately vulnerable Flask apps
│   ├── tests/               500 offline tests
│   ├── kb.sqlite3           Compiled KB (gitignored — build with pull kb)
│   └── kb_cache/            Downloaded tarballs (gitignored)
├── Modules/                 Module packs (Tools/ + Mods/), 49 packs, 93 tools
├── medusa_agent/            THE agent workspace (see Agent Workspace)
├── docs/adr/                Architecture decision records
├── install.sh               One-command installer
├── Dockerfile, docker-compose.yml
├── CHANGELOG.md, CONTRIBUTING.md, SECURITY.md
└── README.md
```

Portability: all paths resolve via `Path(__file__).resolve().parent` — rename
or move the project folder freely. Requirements: `medusa/` and `Modules/` at
the same level; `medusa_agent/` at project root (`medusa/medusa_agent` is a
symlink, auto-repaired at startup).

---

## Troubleshooting

| Symptom | Fix |
|:--------|:---|
| `ModuleNotFoundError: medusa` | Run from the repository root, or use `install.sh`. |
| Interface exits immediately | Run in a real terminal (no pipes); see `medusa doctor`. |
| `Tool calls return Invalid Tool` | Check `medusa modules` — the pack's manifest or its binary may be missing (`medusa tools` marks gaps). |
| nmap/gobuster missing | `brew install nmap gobuster feroxbuster john` / `apt install ...` |
| No API key | Heuristic mode works without one. Add `medusa/.env` (`ZAI_API_KEY=...`) and verify with `medusa env`. |
| Z.ai returns 403 | Endpoint/billing mismatch — set `zai_endpoint` to `coding` (plan) or `paas` (PAYG). See [Providers](#llm-providers). |
| Port 5906 in use | `lsof -i :5906`; other labs use 5900–5905 / 5700 (`medusa labs`). |
| KB not searchable | `medusa pull kb --status` — if not built, run `medusa pull kb`. |

**FAQ:** Can I run without an LLM? Yes — heuristics, detectors, and tool
dispatch all work; the LLM adds reasoning and reporting quality. Is this
legal? Only against systems you own or have written permission to test.

---

## Glossary

| Term | Meaning |
|:-----|:--------|
| Module pack | Self-contained tool bundle under `Modules/Tools` or `Modules/Mods` with a manifest |
| Knowledge base (KB) | Offline FTS5 index of HackTricks/GTFOBins/etc., built by `medusa pull kb` |
| Knowledge graph | Persistent store of findings, flags, patches, attacker profiles shared by both teams |
| Supervisor | Zero-cost pattern detector watching the red team for loops and misses |
| Subagent | Helper agent spawned for a scoped task (max 3 concurrent) |
| Tarpit | Defense that slows an attacker with real response delays |
| Canary token | Honeypot artifact that alerts when touched |
| Response ladder | Blue escalation policy keyed by detector score |
| Engagement | One red or blue operation, start to report |

---

## Contributing & Credits

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Report
vulnerabilities in Medusa itself via [SECURITY.md](SECURITY.md). Decisions are
recorded in [docs/adr/](docs/adr/).

Created by **William Jiang** (lead developer) and **Roland Poon** (design &
project management). Inspired by
[RedAmon](https://github.com/samugit83/redamon) and
[Sakana Fugu](https://github.com/sakana-ai/Fugu). MIT licensed.
