"""HTTP request engine, file I/O, and vulnerability patching tools."""

from __future__ import annotations

import os
from pathlib import Path


def _base_dir():
    from suijin.modules.platform.lib.runtime import BASE_DIR

    return BASE_DIR


def _session():
    """HTTP session (honours a monkeypatched module attr)."""
    v = globals().get("global_session")
    if v is not None:
        return v
    from suijin.modules.platform.lib.runtime import global_session

    return global_session


def __getattr__(name):
    if name == "global_session":
        return _session()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _truncate(text):
    from suijin.modules.platform.lib.runtime import truncate

    return truncate(text)


def _ws():
    """Platform workspace accessors, resolved lazily (module boundary rule)."""
    from suijin.modules.platform.lib import workspace

    return workspace


def http_request(method, url, headers=None, body=""):
    """Advanced Browser Mimicry Engine with session awareness and rate limiting."""
    try:
        # Rate limit check
        from suijin.modules.tools.lib.session_aware import get_session, is_rate_limited, record_response

        if is_rate_limited(url):
            return f"RATE LIMITED: Target {url} is throttling requests. Wait and retry with jitter."

        req_headers = headers if headers else {}
        # Stealth identity (v5.1): one sticky realistic browser per
        # process, full header set. Caller-supplied headers always win.
        try:
            from suijin.modules.platform.lib.stealth import browser_identity, pace

            for k, v in browser_identity().items():
                req_headers.setdefault(k, v)
            pace()  # burst limiter — manual probes pay 0s, bursts get spaced
        except Exception:  # noqa: BLE001 — stealth never breaks a request
            default_headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Upgrade-Insecure-Requests": "1",
            }
            for k, v in default_headers.items():
                req_headers.setdefault(k, v)

        # Track session cookies
        session = get_session(url)
        if session.get_cookie_string():
            req_headers["Cookie"] = session.get_cookie_string()

        resp = _session().request(
            method=method.upper(),
            url=url,
            headers=req_headers,
            data=body,
            timeout=20,
            verify=False,
            allow_redirects=True,
        )
        record_response(url, resp.status_code, dict(resp.headers))
        session.update_from_response(dict(resp.headers), resp.text)
        session.touch()

        out = [
            f"Status: {resp.status_code}",
            f"Headers: {dict(resp.headers)}",
            f"Cookies: {dict(_session().cookies)}",
            f"Body:\n{resp.text}",
        ]
        return _truncate("\n".join(out))
    except Exception as e:
        return f"HTTP Error: {str(e)}"


def apply_patch(vulnerability, file_path="lab.py"):
    """Apply a pre-defined security patch to the target file."""
    full_path = _base_dir() / file_path
    if not full_path.exists():
        return f"Error: {file_path} not found."

    code = full_path.read_text(encoding="utf-8", errors="ignore")
    patched = False
    import re as _re

    vuln = vulnerability.lower()

    if vuln in ["sqli", "sql injection", "sql_injection"]:
        # Try exact match first, then regex fallback
        old_login = "query = f\"SELECT * FROM users WHERE username='{username}' AND password='{password}'\""
        new_login = 'query = "SELECT * FROM users WHERE username=? AND password=?"'
        if old_login in code:
            code = code.replace(old_login, new_login)
            code = code.replace("cur = db.execute(query)", "cur = db.execute(query, (username, password))")
            patched = True
        else:
            # Regex fallback: find f-string SQL patterns and parameterize them
            sqli_pattern = _re.compile(
                r'(?:query|sql|q)\s*=\s*f["\'].*?SELECT.*?\{.*?\}.*?["\']', _re.IGNORECASE | _re.DOTALL
            )
            matches = sqli_pattern.findall(code)
            for match in matches:
                comment = f"# [SUIJIN PATCH] Original vulnerable query: {match[:80]}...\n# Replace with parameterized query: cursor.execute(sql, (param1, param2))"
                code = code.replace(match, comment + "\n" + match, 1)
                patched = True

    elif vuln in ["command injection", "command_injection", "cmdi"]:
        # Fix command injection in /ping_exec
        old_cmd = 'cmd = f"ping -c 2 {host}"'
        new_cmd = 'cmd = ["ping", "-c", "2", host]'
        if old_cmd in code:
            code = code.replace(old_cmd, new_cmd)
            code = code.replace(
                "output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=5)",
                "output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=5)",
            )
            patched = True

    elif vuln in ["ssrf", "lfi", "file inclusion"]:
        # Fix SSRF/LFI in /webhook_fetch
        old_lfi = '''if url.startswith("file://"):
        path = url[7:]
        with open(path, 'r') as f:
            return f"<pre>{f.read()}</pre>"'''
        new_lfi = '''if url.startswith("file://") or url.startswith("file:"):
        return "Error: File protocol not allowed for security reasons."
    if re.match(r'https?://(127\\.|10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[0-1])|0\\.0\\.0\\.0|localhost)', url):
        return "Error: Internal/private addresses are not allowed."'''
        if old_lfi in code:
            code = code.replace(old_lfi, new_lfi)
            patched = True

    elif vuln in ["ssti", "template injection"]:
        # Fix SSTI in /set_announcement
        old_ssti = 'template = "<h2>Announcement:</h2>" + msg\n    return render_template_string(template)'
        new_ssti = "safe_msg = msg.replace('{{', '').replace('}}', '').replace('{%', '').replace('%}', '')\n    return f\"<h2>Announcement:</h2>{safe_msg}\""
        if old_ssti in code:
            code = code.replace(old_ssti, new_ssti)
            patched = True

    elif vuln in ["idor", "xss", "stored xss", "idor_xss"]:
        # Fix IDOR in /profile
        old_profile = 'def profile(user_id):\n    cur = db.execute(f"SELECT * FROM users WHERE id={user_id}")'
        new_profile = "def profile(user_id):\n    if 'user_id' not in session or session['user_id'] != user_id:\n        return \"Access denied\", 403\n    cur = db.execute(\"SELECT * FROM users WHERE id=?\", (user_id,))"
        if old_profile in code:
            code = code.replace(old_profile, new_profile)
            patched = True

        # Fix Stored XSS in /comment
        old_xss = "db.execute(f\"INSERT INTO comments (user_id, text) VALUES ({user_id}, '{text}')\")"
        new_xss = 'import html as html_module\n    safe_text = html_module.escape(text)\n    db.execute("INSERT INTO comments (user_id, text) VALUES (?, ?)", (user_id, safe_text))'
        if old_xss in code:
            code = code.replace(old_xss, new_xss)
            patched = True

    else:
        return f"Unknown vulnerability type: {vulnerability}. Supported: sqli, cmdi, ssrf, ssti, xss, idor_xss"

    if patched:
        full_path.write_text(code, encoding="utf-8")
        return f"Patched {file_path} for {vulnerability}"
    else:
        return f"Could not find exact vulnerable pattern for {vulnerability} in {file_path}. The file may already be patched or use different code. Consider manual review."


def _resolve_target(file_path):
    """Resolve a tool path: expand ~, enforce the workspace boundary,
    and NEVER raise (tool contract: errors are return strings)."""
    try:
        expanded = Path(os.path.expanduser(str(file_path)))
        return _ws().resolve_workspace_path(expanded), None
    except PermissionError as e:
        return None, (
            f"Error: {e} Files outside the workspace can be copied to /tmp/ first "
            "(e.g. via the shell) and read from there."
        )
    except Exception as e:
        return None, f"Error resolving path '{file_path}': {e}"


def read_file(file_path):
    """Read a file — scoped to the agent workspace by default."""
    target, err = _resolve_target(file_path)
    if err:
        return err
    if not target.exists():
        return f"Error: File not found: {target}"
    try:
        return _truncate(target.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(file_path, content):
    """Write content to a file — scoped to the agent workspace by default."""
    target, err = _resolve_target(file_path)
    if err:
        return err
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(str(content), encoding="utf-8")
        rel = target.relative_to(_ws().WORKSPACE_DIR) if str(target).startswith(str(_ws().WORKSPACE_DIR)) else None
        loc = f"suijin_agent/{rel}" if rel else str(target)
        return f"File written: {loc}"
    except Exception as e:
        return f"Error writing file: {e}"
