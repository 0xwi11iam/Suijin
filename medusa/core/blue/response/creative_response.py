"""Creative response — non-blocking countermeasures."""
from __future__ import annotations
from medusa.core.blue.deception.honeypot_factory import generate_honeypot_response
from medusa.core.blue.deception.misinformation import generate_fake_response, generate_fake_500
from medusa.core.blue.deception.canary_token import deploy_canary
import random

def creative_respond(attacker_id: str, request: dict, attack_type: str) -> dict:
    strategies = ["honeypot", "fake_error", "misinformation", "canary", "gaslight"]
    choice = random.choice(strategies)
    if choice == "honeypot":
        return generate_honeypot_response({"path": request.get("path","/")})
    if choice == "fake_error":
        return generate_fake_500()
    if choice == "misinformation":
        return generate_fake_response(attack_type)
    if choice == "canary":
        token = deploy_canary("api_key", f"ak_canary_{random.randint(10000,99999)}")
        return {"status": 200, "body": '{"token":"'+token.value+'","role":"admin"}'}
    return {"status": 200, "body": "OK"}
