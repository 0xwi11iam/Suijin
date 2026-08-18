# ADR-002: JSON Knowledge Graph over Neo4j

- **Status**: Accepted
- **Date**: 2026-07
- **Deciders**: William Jiang

## Context

Suijin needs to persist relationships between entities across a security engagement:

- **Red Team**: Targets → Ports → Services → Vulnerabilities → Exploits → Flags
- **Blue Team**: Attackers → Attacks → Defenses → Intelligence → Countermeasures

The knowledge graph is queried in the hot path — every LLM prompt includes attacker history, every feed decision checks previous flags. It must be:

1. **Fast to query** — sub-millisecond lookups in the decision loop
2. **Zero-setup** — no external services for a local security tool
3. **Serializable** — persist and resume across sessions
4. **Bridgeable** — red team findings must flow to blue team intelligence

## Options Considered

### Option A: Neo4j (Graph Database)

- Full graph database with Cypher query language
- Native graph traversals, indexes, constraints
- Battle-tested at enterprise scale

**Pros**: Real graph queries (traversal, pattern matching), ACID transactions, visualization tools, industry standard
**Cons**: Requires Java runtime + Neo4j server (~500MB memory), startup time 5-15s, operational burden (backups, upgrades, auth), overkill for <10K nodes/edges per session, adds Docker dependency or native install complexity

### Option B: JSON File with In-Memory Index

- Single JSON file (`/tmp/blue_kg.json`) with typed nodes and edges
- In-memory dict indexes: `_attackers_by_ip`, `_attacks_by_ip`, `_defenses_by_ip`
- Persisted on every write, loaded on startup
- Bridged via `bridge_from_red_team()` method

**Pros**: Zero dependencies, zero setup, sub-millisecond lookups via Python dict, trivial serialization (json.dump/load), human-readable on disk, portable across sessions, no memory overhead beyond Python process
**Cons**: No graph traversal queries (no Cypher), O(n) scans for complex relationships, no concurrency control, grows linearly with session data

## Decision

**Chose Option B — JSON file with in-memory index.**

The deciding factors:
1. **Zero-setup requirement** — Suijin target users (bug bounty hunters, CTF players, security researchers) should not need to install and configure a graph database
2. **Scale fits the problem** — a typical engagement has <1,000 nodes and <5,000 edges; Python dict lookups handle this trivially
3. **Session isolation** — each engagement is a fresh session; no need for persistent multi-session storage
4. **Hot-path performance** — `get_attacker_history(ip)` is a single dict lookup, not a network round-trip + query parse + execution
5. **Portability** — the JSON file can be inspected with `cat`, shared with teammates, or archived with engagement reports

The Neo4j code that existed in early versions was dead code — the operational burden of maintaining a Neo4j instance for a local CLI tool was never justified by the query patterns actually used.

## Consequences

### Positive
- `get_attacker_history()` returns in <1ms (single dict lookup)
- Knowledge graph file is human-readable and grep-able
- Zero operational overhead — no server to start, stop, backup, or upgrade
- Bridging red→blue is a single method call merging two JSON structures

### Negative
- Complex graph queries (e.g., "find all attackers who used SQLi AND XSS within 5 minutes") require O(n) Python loops
- No visualization without custom code
- File I/O on every write (mitigated by small file size, typically <100KB)
- No concurrent access support (acceptable for single-agent tool)

## Migration Path

If Suijin evolves to need real graph queries (e.g., multi-session attacker correlation, community detection), the JSON structure maps cleanly to Neo4j nodes and relationships. The `knowledge_graph.py` API (`add_attacker()`, `get_attacker_history()`, `bridge_from_red_team()`) is an abstraction that could be backed by Neo4j without changing callers.

## Alternatives Not Pursued

- **SQLite with JSON columns**: Adds SQL dependency without graph traversal benefits
- **NetworkX in-memory**: Good for graph algorithms but JSON serialization is awkward
- **RedisGraph**: Requires Redis server, same operational burden as Neo4j
