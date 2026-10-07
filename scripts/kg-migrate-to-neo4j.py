#!/usr/bin/env python3
"""One-shot KG migration: knowledge_graph.json -> Neo4j.

Idempotent: MERGE semantics mean re-running updates evidence/confidence
and never duplicates. Run with the server up (docker start suijn-kg).

    python scripts/kg-migrate-to-neo4j.py [--uri bolt://127.0.0.1:7687]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from suijin.modules.redteam.lib.intel.kg_backend import Neo4jKG  # noqa: E402

JSON_PATH = Path(__file__).resolve().parents[1] / "suijin/server/redteam/lib/intel/knowledge_graph.json"
DEFAULT_URI = "bolt://127.0.0.1:7687"


def main() -> int:
    uri = DEFAULT_URI
    if "--uri" in sys.argv:
        uri = sys.argv[sys.argv.index("--uri") + 1]
    password = ""
    if "--password" in sys.argv:
        password = sys.argv[sys.argv.index("--password") + 1]
    if not password:
        import os

        password = os.environ.get("SUIJIN_NEO4J_PASSWORD", "suijn-kg-local")

    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    kg = Neo4jKG(uri, "neo4j", password)
    targets = migrated = 0
    for target, categories in data.items():
        if str(target).startswith("_"):
            continue
        targets += 1
        for ctype, entries in (categories or {}).items():
            if not isinstance(entries, list):
                continue
            for e in entries:
                if not isinstance(e, dict):
                    continue
                kg.add_constraint(
                    target,
                    ctype,
                    str(e.get("rule") or ""),
                    evidence=str(e.get("evidence") or ""),
                    confidence=float(e.get("confidence") or 0.0),
                )
                migrated += 1
    print(f"migrated {migrated} constraints across {targets} targets -> {uri}")
    print("targets now:", len(kg.get_all_targets()))
    kg.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
