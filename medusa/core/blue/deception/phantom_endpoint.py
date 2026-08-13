"""Phantom endpoints — traps that look vulnerable but aren't."""
from __future__ import annotations
import json

PHANTOM_TEMPLATES = {
    "/admin": {"status": 200, "body": "<h1>Admin Panel</h1><form action='/admin/login' method='POST'><input name='user'><input name='pass' type='password'></form>", "headers": {"X-Powered-By": "Express"}},
    "/api/v2/users": {"status": 200, "body": json.dumps([{"id":1,"name":"Admin","role":"superadmin"},{"id":2,"name":"User","role":"user"}])},
    "/.git/HEAD": {"status": 200, "body": "ref: refs/heads/main"},
}

def create_phantom(endpoint_path: str) -> dict:
    for template_path, response in PHANTOM_TEMPLATES.items():
        if template_path in endpoint_path:
            return {"is_phantom": True, "response": response, "template": template_path}
    return {"is_phantom": False}
