# Knowledge Graph Neo4j

30+ node types: Domain→Subdomain→IP→Port→Service→Technology→Endpoint→Parameter→CVE→Vulnerability→Exploit.

Requires: Neo4j running, env vars `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.

```json
{"tool": "kg_insert_node", "args": {"node_type": "Domain", "properties": "{\"name\":\"target.com\"}"}}
{"tool": "kg_query_graph", "args": {"query": "subdomains of target.com"}}
{"tool": "kg_get_attack_paths", "args": {"target": "target.com"}}
```