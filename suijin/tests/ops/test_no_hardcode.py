"""The no-hardcode guard (operator ruling 2026-10-23).

Nothing hardcoded, ever, in tracked files:
- SECRET-SHAPED strings (PATs, API keys, tokens, private keys) — every
  one must be a known synthetic fixture, never a real credential.
- ENGAGEMENT-IDENTIFYING tokens — target/program names from a LOCAL
  gitignored list (agent-vault/targets.txt; absent on CI = that half
  skips). The list itself never enters the repo.
- MACHINE-SPECIFIC paths (/Users/<name>, /home/<name> literals) — the
  tree must run on any machine.

The 2026-10-23 trigger: the knowledge graph's write path pointed into
the package tree, so a live engagement's API keys and target intel were
committed as product data. The mechanism is fixed (workspace-first
graph); this guard is the tripwire for everything else.
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

#: synthetic credential fixtures allowed in the tree (self-evidently
#: fake: all-a filler / EXAMPLE / truncated bodies). A REAL key never
#: belongs here — if one is found, it gets scrubbed, not allowlisted.
SYNTHETIC_SECRETS = {
    "ghp_" + "a" * 36,
    "ghp_" + "abc123def456ghi789jkl012mno345pqr678",
    "sk-abcdefghijklmnopqrst1234x",
    "AIza" + "a" * 35,
    "xoxb-123456789-abcdefghij",
    "-----BEGIN RSA PRIVATE KEY-----",  # the marker alone; real bodies fail the length gate below
}
#: a real private key block is 1000+ chars of base64; fixtures are tiny
_MAX_PRIVATE_KEY_BLOCK = 300

_SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
]

#: lines a target-token hit may appear on WITHOUT counting: vendor
#: service endpoints (installer/download URLs the product itself uses),
#: product tool names, and the safety denylist. Anything else fails.
_VENDOR_LINE_PATTERNS = [
    re.compile(r"dl\.google\.com|cloud\.google\.com|storage\.googleapis"),
    re.compile(r"google-cloud-sdk|googleusercontent"),
    re.compile(r"google_key_probe|google_dork|duckduckgo"),
    re.compile(r"_EXACT_BLOCKED_DOMAINS|_KNOWN_FAMILIES"),
    re.compile(r"github\.com/(0xwi11iam/)?Suijin"),
]

#: machine-specific path shapes (a literal user home in code/docs).
#: /home/<generic> example paths in payloads/docs are fine — the target
#: is REAL usernames baked into the tree.
_GENERIC_HOMES = {"user", "eng", "test", "tests", "demo", "example", "alice", "bob", "oai", "sudo"}
_MACHINE_PATH = re.compile(r"/Users/[a-z][\w.-]*/|/home/[a-z][\w.-]*/")

#: files exempt from the engagement-token scan, with reasons — kept
#: explicit and tiny so the list can't rot:
#:   hard_guardrail.py — the SAFETY denylist must name the domains it
#:   blocks (a config-driven denylist is a disable-by-accident footgun).
_EXEMPT_FILES = {"suijin/server/platform/lib/helpers/hard_guardrail.py"}

_TEXT_SUFFIXES = {".py", ".json", ".md", ".sh", ".toml", ".yml", ".yaml", ".txt", ".cfg", ".ini"}


def _tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.splitlines()
    return [REPO / p for p in out if (REPO / p).suffix in _TEXT_SUFFIXES and (REPO / p).is_file()]


def _local_targets() -> list[str]:
    """Engagement tokens from the operator's local vault — never in git."""
    p = REPO / "agent-vault" / "targets.txt"
    if not p.is_file():
        return []
    return [ln.strip() for ln in p.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


class TestNoHardcode:
    def test_no_real_credentials_in_tree(self):
        offenders = []
        for f in _tracked_files():
            text = f.read_text(errors="replace")
            for pat in _SECRET_PATTERNS:
                for hit in pat.findall(text):
                    if hit not in SYNTHETIC_SECRETS:
                        offenders.append(f"{f.relative_to(REPO)}: {hit[:12]}…")
            # a real private key body (1000+ chars of base64) is a secret
            # even when the marker line is known; fixtures are tiny
            for block in re.findall(r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----", text):
                if len(block) > _MAX_PRIVATE_KEY_BLOCK:
                    offenders.append(f"{f.relative_to(REPO)}: private-key block ({len(block)}B)")
        assert not offenders, "secret-shaped strings in tracked files (scrub or synthetic): " + "; ".join(offenders)

    def test_no_engagement_targets_in_tree(self):
        import pytest

        targets = _local_targets()
        if not targets:
            pytest.skip("no local agent-vault/targets.txt — engagement-token scan skipped")
        offenders = []
        for f in _tracked_files():
            rel = str(f.relative_to(REPO))
            if rel in _EXEMPT_FILES:
                continue
            for line in f.read_text(errors="replace").splitlines():
                if any(t in line for t in targets) and not any(p.search(line) for p in _VENDOR_LINE_PATTERNS):
                    offenders.append(f"{rel}: {line.strip()[:90]}")
        assert not offenders, "engagement-identifying tokens in tracked files: " + " | ".join(offenders[:10])

    def test_no_machine_specific_paths(self):
        offenders = []
        for f in _tracked_files():
            text = f.read_text(errors="replace")
            for m in _MACHINE_PATH.finditer(text):
                seg = m.group(0).rstrip("/")
                if seg.startswith("/home/") and seg.split("/")[2] in _GENERIC_HOMES:
                    continue
                offenders.append(f"{f.relative_to(REPO)}: {m.group(0)}")
        assert not offenders, "machine-specific paths in tracked files: " + "; ".join(offenders[:10])

    def test_version_sources_agree(self):
        """The v7.3.0 release died on PyPI: pyproject's static version
        drifted from version.json — the wheel built 7.2.0 and the upload
        was rejected as a duplicate. They must ALWAYS match."""
        import json
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parents[3]
        pj = re.search(r'^version = "(.+)"', (repo / "pyproject.toml").read_text(), re.M).group(1)
        vj = json.loads((repo / "suijin" / "version.json").read_text())["version"]
        assert pj == vj, f"VERSION DRIFT: pyproject={pj} version.json={vj}"

    def test_knowledge_graph_writes_never_touch_the_package_seed(self):
        """The mechanism behind the 2026-10-23 leak: learned intel must
        land in the WORKSPACE, never in the package tree."""
        from suijin.modules.redteam.lib.intel import knowledge_graph as kg

        assert kg.SEED_PATH == kg.BASE_DIR / "knowledge_graph.json"
        assert kg._default_graph_path() != kg.SEED_PATH, "default write path must be workspace-first"
