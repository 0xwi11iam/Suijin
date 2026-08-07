"""Blue team tool registry — different tools than red team."""
BLUE_TOOLS = {
    "codebase_scan": "Scan target codebase for all endpoints and vulnerabilities",
    "traffic_normalize": "Learn normal traffic patterns for an endpoint",
    "traffic_score": "Score a request 1-10 for anomaly severity",
    "classify_attack": "Classify attack type from request payload",
    "deploy_honeypot": "Create a decoy response with canary tokens",
    "shadow_redirect": "Transparently redirect attacker to isolated environment",
    "engage_tarpit": "Slow attacker with delayed responses",
    "block_ip": "Block an IP at the firewall level",
    "search_logs": "Search access logs for patterns",
    "correlate_sessions": "Cross-reference attacker activity across endpoints",
    "generate_patch": "Generate a security fix for a confirmed vulnerability",
    "deploy_patch": "Deploy a patch to production (requires operator approval)",
}
