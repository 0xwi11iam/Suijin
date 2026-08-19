# Knowledge Graph (`check_knowledge`, `record_finding`)

Persistent target constraints. Check before payloads, record after verification.

## Installation

Built-in. Data stored in `knowledge_graph.json`.

## Usage

### Check constraints
```json
{"tool": "check_knowledge", "args": {"target": "10.0.0.5"}}
```

### Check specific payload
```json
{"tool": "check_knowledge", "args": {"target": "10.0.0.5", "payload": "' OR 1=1"}}
```

### Record a finding
```json
{"tool": "record_finding", "args": {"target": "10.0.0.5", "finding_type": "blocks", "rule": "' OR 1=1 --", "evidence": "403 WAF block confirmed"}}
```

## Finding Types

| Type | When to use |
|------|-------------|
| `blocks` | WAF/filter blocked a pattern |
| `rate_limit` | Server throttled requests |
| `waf` | WAF type identified |
| `verified_cve` | CVE confirmed working |
| `false_positive` | Anomaly was not real |
| `bypass` | Working bypass strategy |
| `behavior` | Observable behavior |

## SOP

1. Fingerprint → `search_cve`
2. Before payload → `check_knowledge`
3. After verify → `record_finding`
4. Never repeat blocked patterns
