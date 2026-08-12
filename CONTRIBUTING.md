# Contributing to Medusa

Thank you for your interest in contributing! Medusa is a dual-mode (Red Team + Blue Team) autonomous security platform. This guide covers everything you need to start contributing.

## Developer Setup

### Prerequisites

- **Python 3.10+** (developed on 3.14, CI tests 3.10/3.11/3.12)
- **macOS / Linux** (Windows works but some tools are untested)
- **API key** (DeepSeek recommended; HuggingFace, Gemini, Anthropic also supported)

### Quick Start

```bash
git clone https://github.com/0xwi11iam/Medusa.git
cd medusa-security
python3 -m venv .venv && source .venv/bin/activate
pip install -r medusa/requirements.txt

# Set your API key
echo "DEEPSEEK_API_KEY=sk-..." > medusa/.env

# Verify setup
python3 -m pytest medusa/tests/ -q
```

### Project Structure

```
medusa/
├── core/           # State machine, red/blue teamers, config, constants
│   └── blue/       # Blue team SOC modules (ai_engine, feed, proxy, defense, soc)
├── tools/          # 85-tool dispatch, guardrails, workspace, providers
├── tests/          # pytest suite (7 files, 134 tests)
├── lab/            # Deliberately vulnerable labs (25-endpoint Flask app, CloudBoard Next)
├── skills/         # Attack skill definitions (SQLi, XSS, SSRF, SSTI, etc.)
├── prompts/        # LLM system prompts (red_team, blue_team)
└── main.py         # TUI entry point (Rich console)
Modules/            # Tool wrappers (nmap, sqlmap, hydra, metasploit, etc.)
medusa_agent/       # Agent workspace (outputs, payloads, scripts)
```

## Development Workflow

### Running Tests

```bash
# Full suite (~1 second)
python3 -m pytest medusa/tests/ -q

# Specific file
python3 -m pytest medusa/tests/test_tools.py -v

# Skip slow/AI tests
python3 -m pytest medusa/tests/ -q -m "not slow and not ai"

# With coverage
python3 -m pytest medusa/tests/ --cov=medusa --cov-report=term-missing
```

### Linting & Type Checking

```bash
# Ruff (configured in pyproject.toml)
pip install ruff
ruff check medusa/
ruff format --check medusa/

# Pyright (configured in pyproject.toml)
pyright medusa/
```

### Pre-commit Hooks

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## Architecture

### Red Team Pipeline

```
main.py → redteamer.py → LangGraph State Machine
  ├── initialize     # Load config, skills, tools
  ├── think          # LLM ReAct loop with 7 action types
  ├── execute_tool   # Dispatch to 85 tools
  └── generate_response  # Format output, update audit trail
```

### Blue Team Pipeline

```
main.py → blueteamer.py → Traffic Interception
  ├── proxy.py       # HTTP forward proxy (port 8080 → port 5906)
  ├── tui/feed.py    # LiveFeed tier router (18 pattern detectors → AI engine)
  ├── ai_engine.py   # LLM decision maker (FLAGGED/NOT_FLAGGED → DECEIVE/BLOCK/PATCH)
  └── defense/       # Tarpit, firewall, WAF, deception engine
```

### Key Design Decisions

See [`docs/adr/`](docs/adr/) for Architecture Decision Records:
- [ADR-001](docs/adr/001-langgraph-over-asyncio.md) — Why LangGraph instead of raw asyncio loop
- [ADR-002](docs/adr/002-json-kg-over-neo4j.md) — Why JSON knowledge graph instead of Neo4j

## Adding a New Tool

1. Create wrapper in `Modules/` (or use existing wrappers in `medusa/tools/`)
2. Register in `medusa/tools/dispatch.py` tool routing table
3. Add skill definition in `medusa/skills/` if it's an attack technique
4. Add tests in `medusa/tests/`

## Adding a New Attack Pattern Detector

1. Add regex pattern to `_ATTACK_PATTERNS` in `medusa/core/blue/tui/feed.py`
2. Set appropriate weight (1–5)
3. Add test in `medusa/tests/test_blue_team.py`
4. Update the pattern table in `README.md`

## Code Style

- **Line length**: 120 characters
- **Quotes**: Double quotes (configured in ruff)
- **Error handling**: No bare `except:` — always `except Exception` with logging
- **Types**: Use `from __future__ import annotations` for forward references
- **Constants**: Use `medusa.core.constants` — never hardcode model IDs, ports, or thresholds
- **Config**: Validate with Pydantic models in `medusa/core/config_models.py`
- **Paths**: Use `medusa/core/paths.py` for tmp paths, `medusa/tools/workspace.py` for workspace paths

## Commit Conventions

```
feat(scope): description      # New feature
fix(scope): description       # Bug fix
refactor(scope): description  # Code restructuring
test(scope): description      # Test additions/changes
docs(scope): description      # Documentation
chore(scope): description     # Build, CI, deps
```

## CI/CD

GitHub Actions runs on every push:
- **Matrix**: Python 3.10, 3.11, 3.12
- **Tests**: pytest with coverage
- **Lint**: ruff check
- **Security**: pip-audit dependency scan

See `.github/workflows/ci.yml`.

## Questions?

Open an issue on GitHub or start a discussion. For security-sensitive matters, see [SECURITY.md](SECURITY.md).
