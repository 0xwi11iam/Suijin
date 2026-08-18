"""Honeypot factory — dynamically generate realistic decoys."""

from __future__ import annotations

import json
import random


def generate_honeypot_response(endpoint: dict) -> dict:
    path = endpoint.get("path", "/")
    if "admin" in path.lower():
        return {
            "status": 200,
            "body": json.dumps(
                {
                    "users": [
                        {
                            "id": 99,
                            "name": "admin_backup",
                            "role": "admin",
                            "api_key": "fake_canary_" + random.randint(1000, 9999).__str__(),
                        }
                    ]
                }
            ),
        }
    if "api" in path.lower():
        return {
            "status": 200,
            "body": json.dumps(
                {"data": [], "meta": {"total": 0, "debug_token": "canary_" + random.randint(1000, 9999).__str__()}}
            ),
        }
    return {"status": 200, "body": "OK", "headers": {"X-Debug-Token": "canary_" + random.randint(1000, 9999).__str__()}}


def create_decoy_endpoint(base_path: str) -> dict:
    decoys = ["/admin", "/.git/HEAD", "/wp-admin", "/api/debug", "/backup.sql", "/config.yml", "/.env"]
    path = random.choice(decoys)
    return {"path": base_path.rstrip("/") + path, "method": "GET", "is_decoy": True}
