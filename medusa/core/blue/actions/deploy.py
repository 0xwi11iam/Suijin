"""
medusa/core/blue/actions/deploy.py — Real defensive actions.

When the AI decides to respond to an attack, these functions actually
engineer and deploy the countermeasure: write honeypot endpoints to the
target codebase, patch vulnerabilities, deploy canary tokens, create
deception responses.

Imports the subagent's pre-built assets (honeypot_code, patch_code,
deception_response) that were generated during the analysis phase.
"""
import json, os, time, re
from pathlib import Path
from typing import Optional


def deploy_honeypot(target_path: str, subagent, attacker_ip: str) -> dict:
    """Write the subagent's pre-built honeypot endpoint to the target codebase.

    Creates a new route that looks like the real endpoint but:
    - Returns canary token data (fake credentials that alert when used)
    - Logs everything the attacker does
    - Never touches real data
    """
    if not subagent or not subagent.honeypot_code:
        return {"status": "skipped", "reason": "No honeypot code available"}

    ep = subagent.endpoint
    original_path = ep.get("path", "/")
    file_path = ep.get("file", "")
    framework = subagent.framework or ep.get("framework", "unknown")

    if not file_path or not os.path.exists(file_path):
        return {"status": "skipped", "reason": f"Source file not found: {file_path}"}

    try:
        # Generate a honeypot route path (e.g., /api/users → /api/users_backup)
        honeypot_path = _make_honeypot_path(original_path)

        # Adapt the honeypot code for the framework
        if framework == "flask":
            route_code = _wrap_flask_honeypot(subagent.honeypot_code, honeypot_path)
        elif framework in ("fastapi", "django"):
            route_code = _wrap_fastapi_honeypot(subagent.honeypot_code, honeypot_path)
        elif framework in ("express", "javascript", "node"):
            route_code = _wrap_express_honeypot(subagent.honeypot_code, honeypot_path)
        else:
            # Generic — append as commented code block, usable with manual integration
            route_code = _wrap_generic_honeypot(subagent.honeypot_code, honeypot_path, framework)

        # Append to the source file
        with open(file_path, "a") as f:
            f.write(f"\n# Medusa honeypot deployed {time.strftime('%Y-%m-%d %H:%M:%S')} — attacker {attacker_ip}\n")
            f.write(route_code)
            f.write(f"\n# End honeypot\n")

        return {
            "status": "deployed",
            "honeypot_path": honeypot_path,
            "file": file_path,
            "framework": framework,
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def deploy_patch(target_path: str, subagent) -> dict:
    """Apply the subagent's pre-built patch to fix the vulnerability.

    Replaces the vulnerable handler code with the fixed version.
    Uses a marker-based approach to find and replace the handler.
    """
    if not subagent or not subagent.patch_code:
        return {"status": "skipped", "reason": "No patch code available"}

    ep = subagent.endpoint
    file_path = ep.get("file", "")
    handler_code = subagent.handler_code

    if not file_path or not os.path.exists(file_path):
        return {"status": "skipped", "reason": f"Source file not found: {file_path}"}

    try:
        original = Path(file_path).read_text(errors="ignore")

        if handler_code and handler_code in original:
            # Replace the vulnerable handler with the patched version
            patched = original.replace(handler_code, subagent.patch_code)
            Path(file_path).write_text(patched)
            return {"status": "patched", "file": file_path}
        else:
            # Can't find exact handler — append the patch as a comment and note
            with open(file_path, "a") as f:
                f.write(f"\n# === MEDUSA PATCH for {ep.get('path','/')} ===\n")
                f.write(f"# Original handler was vulnerable. Patch code:\n")
                f.write(f"# {subagent.patch_code[:500]}\n")
            return {"status": "noted", "file": file_path, "reason": "Handler not found for exact replacement — patch appended as comment"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def deploy_canary_tokens(target_path: str, attacker_ip: str) -> dict:
    """Create canary token files that alert if accessed.

    Writes fake credential files to common locations. If an attacker
    reads them and tries to use the credentials, we know.
    """
    import uuid
    tokens = {
        ".env.canary": f"# CANARY TOKEN — deployed {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"AWS_ACCESS_KEY_ID=AKIA_CANARY_{uuid.uuid4().hex[:8]}\n"
                        f"AWS_SECRET_ACCESS_KEY=canary_{uuid.uuid4().hex[:16]}\n"
                        f"DATABASE_URL=postgres://admin:canary_{uuid.uuid4().hex[:8]}@localhost/canary_db\n",
        "api_keys.json": json.dumps({
            "_canary": True,
            "deployed_for": attacker_ip,
            "stripe_key": f"sk_live_canary_{uuid.uuid4().hex[:12]}",
            "github_token": f"ghp_canary_{uuid.uuid4().hex[:16]}",
        }, indent=2),
    }

    deployed = []
    for filename, content in tokens.items():
        path = Path(target_path) / filename
        try:
            path.write_text(content)
            deployed.append(str(path))
        except Exception:
            pass

    return {"status": "deployed" if deployed else "failed", "files": deployed}


def deploy_deception_data(target_path: str, subagent, attacker_ip: str) -> dict:
    """Deploy deception — fake data that looks real but traps attackers.

    Uses the subagent's pre-built deception_response template.
    """
    if not subagent or not subagent.deception_response:
        return {"status": "skipped", "reason": "No deception template available"}

    try:
        # Write deception data to a JSON file the app can serve
        decoy_file = Path(target_path) / f".medusa_decoy_{subagent.rank:02d}.json"
        decoy_data = {
            "deployed_for": attacker_ip,
            "deployed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "endpoint": subagent.endpoint.get("path", "/"),
            "response": subagent.deception_response,
        }
        decoy_file.write_text(json.dumps(decoy_data, indent=2))
        return {"status": "deployed", "file": str(decoy_file)}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def _make_honeypot_path(original: str) -> str:
    """Generate a honeypot route path from the original endpoint."""
    # Strip Flask/Express pattern variables
    clean = re.sub(r'<[^>]+>', 'data', original)
    # Add honeypot suffix
    if clean.endswith("/"):
        return clean + "_backup"
    return clean + "/_backup"


def _wrap_flask_honeypot(code: str, path: str) -> str:
    """Wrap honeypot code in a Flask route decorator."""
    # If code already has @app.route, use as-is
    if "@app.route" in code or "@route" in code:
        return code.replace(
            re.findall(r'@\w*route\s*\(\s*["\'][^"\']+["\']', code)[0] if re.findall(r'@\w*route\s*\(\s*["\'][^"\']+["\']', code) else "",
            f'@app.route("{path}")',
            1
        ) if re.findall(r'@\w*route\s*\(\s*["\'][^"\']+["\']', code) else f'@app.route("{path}")\n{code}'
    # Otherwise wrap it
    return f"""
@app.route("{path}", methods=["GET", "POST"])
def honeypot_{path.replace('/', '_').replace('-', '_')}():
    {code}
    return {{"status": "ok"}}
"""


def _wrap_fastapi_honeypot(code: str, path: str) -> str:
    """Wrap honeypot code in a FastAPI route decorator."""
    return f"""
@app.get("{path}")
@app.post("{path}")
async def honeypot_{path.replace('/', '_').replace('-', '_')}():
    {code}
    return {{"status": "ok"}}
"""


def _wrap_express_honeypot(code: str, path: str) -> str:
    """Wrap honeypot code in an Express.js route."""
    safe_name = path.replace('/', '_').replace('-', '_').replace(':', '')
    return f"""
// === MEDUSA HONEYPOT ===
app.all('{path}', (req, res) => {{
    // Honeypot — logs attacker, returns canary data
    console.log('[MEDUSA HONEYPOT] Attacker hit {path}');
    {code}
    res.json({{"status": "ok"}});
}});
"""


def _wrap_generic_honeypot(code: str, path: str, framework: str) -> str:
    """Generic honeypot wrapper for any framework — embeds as a well-documented code block."""
    return f"""
# ============================================================
# MEDUSA HONEYPOT — {path}
# Framework: {framework}
# Auto-deployed in response to attack.
# This endpoint looks real but traps attackers.
# Integrate into your {framework} route handler.
# ============================================================
# HONEYPOT CODE:
{code}
# ============================================================
"""
