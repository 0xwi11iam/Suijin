"""WAF rule generator — ModSecurity/Cloudflare rules from attack patterns."""
def generate_waf_rule(attack_type: str, pattern: str) -> str:
    if attack_type == "sqli":
        return f'SecRule REQUEST_BODY "@rx {pattern}" "id:10001,deny,status:403,msg:\'SQL Injection detected\'"'
    if attack_type == "xss":
        return f'SecRule REQUEST_BODY "@rx {pattern}" "id:10002,deny,status:403,msg:\'XSS detected\'"'
    return f'SecRule REQUEST_URI "@rx {pattern}" "id:10003,deny,status:403"'
