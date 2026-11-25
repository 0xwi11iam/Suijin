# Knowledge graph

The live graph is targets → constraints (rule, evidence, confidence, first
seen, last seen). Agents read it before generating payloads and write to it
when they learn something. A finding's catalog and the memory page read the
same graph.

## Backends

- **ladybug** — embedded Cypher database, one file at
  `~/.suijin/intel/knowledge_graph.ladybug`. No Docker, no server. Each
  operation opens the file, runs, and closes; a short lock retry lets the
  web process and agent subprocesses share it.
- **json** — `~/.suijin/intel/knowledge_graph.json`, human-readable fallback
  and the operator-owned `methodology` branch.
- **neo4j** — optional external server; see `kg_backend.py`.

Switch in `~/.suijin/config/config.json`:

```json
{ "kg_backend": "ladybug" }
```

Optional path override: `"ladybug_path": "~/.suijin/intel/knowledge_graph.ladybug"`.

## Migration

```bash
python scripts/kg-migrate-to-ladybug.py
```

Reads the runtime JSON file, skips `methodology` and internal keys, and
MERGEs every target/constraint into the embedded graph. Re-running is safe.
The JSON file stays in place as the fallback and as the methodology source.
