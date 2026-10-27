"""request_auth — the agent's authenticated-testing entry point.

When the agent hits a login wall (401/403/redirect-to-login), it calls
request_auth(target, hint). The engagement pauses, the operator is shown
the auth panel with three handoff modes:

  [1] CREDENTIALS — operator types user/pass, agent handles the login POST
  [2] TOKEN/COOKIE — operator pastes a Bearer token, session cookie, or API key
  [3] BROWSER — agent opens a Chromium window (Playwright), operator logs
      in interactively (2FA/CAPTCHA/social auth), cookies are captured

The credential is stored in the box's credential vault and the agent
references it by name via http_request(credential="name").

On the HOST: the browser pops up as a normal GUI window.
IN THE BOX: the browser runs on the HOST (boxes are headless) and the
captured session is sent to the box through the workspace.
"""

from __future__ import annotations

import contextlib
import json


def request_auth(
    target: str,
    hint: str = "",
    login_url: str = "",
    credential_name: str = "",
    config=None,
    route_fn=None,
) -> str:
    """Request authenticated access from the operator.

    Args:
        target: the host/URL that needs auth (e.g. "api.example.com")
        hint: what auth is needed (e.g. "login form at /login", "Bearer token required")
        login_url: the URL to navigate to for browser-based login
        credential_name: optional name for the stored credential (defaults to target hostname)

    Returns:
        Instructions for the agent on how to use the credential, or an
        error if the operator declined.
    """
    target = str(target or "").strip()
    if not target:
        return "Error: target is required (the host that needs authentication)"

    hint = str(hint or "authentication required").strip()
    login_url = str(login_url or "").strip() or f"https://{target}/login"
    cred_name = str(credential_name or "").strip() or target.split("/")[0].replace(":", "_")

    # Store the auth request in the workspace for the operator console / TUI
    _write_auth_request(target, hint, login_url, cred_name)

    # BROWSER CAPTURE: if playwright is available, open a real browser
    # on the HOST (the operator logs in — 2FA/CAPTCHA handled by the
    # human) and capture the session automatically. In the BOX, the
    # browser opens on the HOST through the live-mounted workspace.
    with contextlib.suppress(Exception):
        from suijin.server.tools.lib.browser_auth import browser_capture

        result = browser_capture(login_url, cred_name, target_host=target)
        if result.get("success"):
            cookies = result.get("cookies", [])
            cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            use_var = cred_name
            with contextlib.suppress(Exception):
                from suijin.modules.credential_store.main import creds_add, env_name_for

                # SERVER-SIDE STORE (2026-10-27): store the captured cookie
                # here — the literal never passes through the agent's text
                # at all. The agent references $SUIJIN_CRED_<NAME> only.
                creds_add(
                    service=cred_name,
                    cred_type="cookie",
                    value=cookie_header,
                    notes=f"browser capture: {target}",
                )
                use_var = env_name_for(cred_name)
            return (
                f"AUTH CAPTURED via browser login — {len(cookies)} cookies from {result.get('url', '')}\n"
                f"Stored as credential '{cred_name}'. In commands use ${use_var} "
                f"(exported to execute_terminal automatically — never paste the literal).\n"
                f'For HTTP requests use: http_request(url="https://{target}/api", credential="{cred_name}")\n'
                f"Full session saved: auth/{cred_name}_session.json"
            )
        if result.get("error") and "not installed" not in str(result.get("error", "")):
            return f"AUTH FAILED: {result['error']}"

    # FALLBACK: instruct the agent to ask the operator directly
    env_var = cred_name
    with contextlib.suppress(Exception):
        from suijin.modules.credential_store.main import env_name_for

        env_var = env_name_for(cred_name)
    return (
        f"AUTH NEEDED: {target} — {hint}\n"
        f"Login URL: {login_url}\n"
        f"Credential name: {cred_name}\n\n"
        f"Call ask_operator with this question:\n"
        f'"I need authenticated access to {target}. {hint}. '
        f"Please provide credentials (user/pass), a session token/cookie, "
        f"or type 'browser' to open a login window I can watch.\"\n\n"
        f"After receiving credentials, store them with:\n"
        f'  creds_add(service="{cred_name}", type="<bearer|cookie|basic>", value="<credential>")\n'
        f"Then use ${env_var} in execute_terminal commands (auto-exported — never paste the "
        f'literal into commands), or http_request(url="https://{target}/api", credential="{cred_name}")'
    )


def _write_auth_request(target: str, hint: str, login_url: str, cred_name: str) -> None:
    """Persist the auth request for the operator console to pick up."""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.workspace import artifact_dir

        p = artifact_dir("auth") / "pending.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        requests = []
        if p.exists():
            with contextlib.suppress(Exception):
                requests = json.loads(p.read_text())
        requests.append(
            {
                "target": target,
                "hint": hint,
                "login_url": login_url,
                "credential_name": cred_name,
                "status": "pending",
            }
        )
        p.write_text(json.dumps(requests, indent=2))
