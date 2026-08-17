"""Misinformation engine — feed false data to attackers."""
from __future__ import annotations

import json
import random


def generate_fake_response(attack_type: str) -> dict:
    if "sqli" in attack_type.lower():
        return {"status": 500, "body": json.dumps({"error":"Database connection lost. Admin notified.","db_host":"db-internal.corp.local","db_user":"prod_admin"})}
    if "xss" in attack_type.lower():
        return {"status": 200, "body": "<script>console.log('XSS detected. Admin session: '+document.cookie)</script>Debug mode active"}
    if "ssrf" in attack_type.lower():
        return {"status": 200, "body": json.dumps({"internal_services":["http://admin-panel.internal:8080","http://db-master.internal:5432"]})}
    return {"status": 200, "body": json.dumps({"status":"ok","debug":True,"admin_email":"admin@corp.com"})}

def generate_fake_500() -> dict:
    templates = [
        {"status": 500, "body": '<h1>500 Internal Server Error</h1><pre>Stack trace: File "/app/auth.py", line 42, in login\\n    result = db.execute(query)\\nsqlite3.OperationalError: database is locked</pre>'},
        {"status": 503, "body": "Service Temporarily Unavailable. Retry-After: 300"},
        {"status": 500, "body": json.dumps({"error":"Out of memory","memory_usage_mb": 3892, "limit_mb": 4096})},
    ]
    return random.choice(templates)
