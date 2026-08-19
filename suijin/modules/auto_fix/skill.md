# Auto-Fix Pipeline

CypherFix-style automated remediation pipeline: triage → fix → verify.

```json
{"tool": "autofix_triage", "args": {"findings_json": "[{\"type\":\"sqli\",\"severity\":\"critical\",\"endpoint\":\"/login\"}]"}}
{"tool": "autofix_generate_fix", "args": {"vulnerability_type": "sqli", "file_path": "app.py", "line_number": 42}}
{"tool": "autofix_verify_fix", "args": {"fix_content": "..."}}
```

Supports: sqli, xss, rce, path_traversal, ssrf, idor, deserialization, xxe, csrf, mass_assignment, jwt.