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

    # The actual operator interaction happens through the ask_operator
    # mechanism — we return a structured prompt the think-node turns into
    # an ask. But for now, return a direct instruction that the agent
    # should call ask_operator with the auth question.
    return (
        f"AUTH NEEDED: {target} — {hint}\n"
        f"Login URL: {login_url}\n"
        f"Credential name: {cred_name}\n\n"
        f"Call ask_operator with this question:\n"
        f'"I need authenticated access to {target}. {hint}. '
        f"Please provide credentials (user/pass), a session token/cookie, "
        f"or type 'browser' to open a login window I can watch.\"\n\n"
        f"After receiving credentials, store them with:\n"
        f'  creds_add(name="{cred_name}", type="<bearer|cookie|basic>", value="<credential>")\n'
        f'Then use: http_request(url="https://{target}/api", credential="{cred_name}")'
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
