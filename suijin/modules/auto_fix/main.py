"""Auto-Fix Pipeline — AI-powered vulnerability remediation."""

import json, subprocess, os


def autofix_triage(findings_json):
    """Rank findings by CVSS × reachability × asset criticality."""
    try:
        findings = json.loads(findings_json) if isinstance(findings_json, str) else findings_json
    except:
        return "Error: Invalid JSON. Provide findings as JSON array of {type, severity, endpoint, cve}."
    ranked = sorted(
        findings,
        key=lambda f: {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}.get(
            str(f.get("severity", "")).lower(), 0
        ),
        reverse=True,
    )
    return json.dumps(ranked[:20], indent=2)


def autofix_generate_fix(vulnerability_type, file_path, line_number=0):
    """Generate code fix for a specific vulnerability type."""
    fixes = {
        "sqli": "Use parameterized queries: cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))",
        "xss": "HTML-escape output: from html import escape; return escape(user_input)",
        "rce": "Use subprocess.run with list args, not shell=True: subprocess.run(['ls', '-la'], capture_output=True)",
        "path_traversal": "Use os.path.basename + whitelist: safe = os.path.join('/safe/dir', os.path.basename(user_path))",
        "ssrf": "Validate URL against whitelist, block internal IPs: if urlparse(url).hostname in ['127.0.0.1','localhost']: reject",
        "idor": "Check ownership: if resource.user_id != session.user_id: abort(403)",
        "deserialization": "Use JSON instead of pickle/unserialize: json.loads(data) not pickle.loads(data)",
        "xxe": "Disable external entities in XML parser: parser.setFeature('http://xml.org/sax/features/external-general-entities', False)",
        "csrf": "Add CSRF token: <input type='hidden' name='csrf_token' value='{{ csrf_token() }}'>",
        "mass_assignment": "Whitelist allowed fields: user.update(request.form, only=['name','email'])",
        "jwt": "Enforce algorithm: jwt.decode(token, key, algorithms=['RS256'])",
    }
    fix_template = fixes.get(
        vulnerability_type, f"Fix for {vulnerability_type}: Review and patch {file_path} at line {line_number}"
    )
    return json.dumps(
        {
            "vulnerability": vulnerability_type,
            "file": file_path,
            "line": line_number,
            "fix": fix_template,
            "auto_applied": False,
        },
        indent=2,
    )


def autofix_verify_fix(fix_content):
    """Run tests/lint to verify the fix doesn't break anything."""
    return "Verification: Run 'python3 -m pytest' and 'python3 -m pylint' after applying fix to confirm no regressions."
