"""Neo4j Knowledge Graph — structured attack surface mapping."""

import json, os

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASS = os.environ.get("NEO4J_PASSWORD", "neo4j")

# 30+ Node Types:
NODE_TYPES = [
    "Domain",
    "Subdomain",
    "IP",
    "Port",
    "Service",
    "Technology",
    "Endpoint",
    "Parameter",
    "CVE",
    "Vulnerability",
    "Exploit",
    "Credential",
    "Hash",
    "Session",
    "User",
    "Group",
    "Computer",
    "Certificate",
    "Cookie",
    "Header",
    "Finding",
    "AttackPath",
    "CWE",
    "CAPEC",
    "MITRE",
    "CloudResource",
    "Container",
    "Registry",
    "Secret",
    "Config",
    "LogEntry",
]


def _neo4j_query(cypher, params=None):
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
        with driver.session() as session:
            result = session.run(cypher, params or {})
            records = [dict(r) for r in result]
        driver.close()
        return json.dumps(records, indent=2, default=str)
    except ImportError:
        return "Error: neo4j Python driver not installed. Run: pip install neo4j"
    except Exception as e:
        return f"Neo4j error: {e}"


def kg_insert_node(node_type, properties="{}"):
    if node_type not in NODE_TYPES:
        return f"Error: Unknown node type '{node_type}'. Valid types: {', '.join(NODE_TYPES[:10])}..."
    try:
        props = json.loads(properties) if isinstance(properties, str) else properties
        prop_str = ", ".join(f"{k}: ${k}" for k in props)
        cypher = f"CREATE (n:{node_type} {{{prop_str}}}) RETURN n"
        return _neo4j_query(cypher, props)
    except Exception as e:
        return f"Insert error: {e}"


def kg_query_graph(query):
    # Simple natural-language-to-Cypher mapping
    ql = query.lower()
    if "subdomain" in ql and "domain" in ql:
        cypher = "MATCH (d:Domain)-[:HAS]->(s:Subdomain) RETURN d,s LIMIT 50"
    elif "port" in ql or "service" in ql:
        cypher = "MATCH (ip:IP)-[:HAS_PORT]->(p:Port)-[:RUNS]->(s:Service) RETURN ip,p,s LIMIT 50"
    elif "vuln" in ql or "cve" in ql:
        cypher = "MATCH (s:Service)-[:HAS_VULN]->(v:Vulnerability)-[:HAS_CVE]->(c:CVE) RETURN s,v,c LIMIT 50"
    elif "attack" in ql or "path" in ql:
        cypher = "MATCH path = (d:Domain)-[*1..5]->(v:Vulnerability) RETURN path LIMIT 25"
    else:
        cypher = "MATCH (n) RETURN DISTINCT labels(n) as node_types, count(n) as count"
    return _neo4j_query(cypher)


def kg_get_attack_paths(target):
    cypher = f"MATCH path = (start)-[*1..6]->(v:Vulnerability) WHERE start.name CONTAINS '{target}' OR start.ip = '{target}' RETURN path LIMIT 25"
    return _neo4j_query(cypher)
