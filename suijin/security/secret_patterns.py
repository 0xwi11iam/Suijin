"""
suijin/security/secret_patterns.py — Consolidated security patterns.
Merges functionality previously spread across redamon bridge modules:
- Secret regex patterns (github_secret_hunt)
- Credential classification (trufflehog_scan)
- CVE-to-attack mapping (gvm_scan)
"""
import math
import re
from enum import Enum

# ── Secret Detection Patterns ─────────────────────────────────────────────────

SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"(?:AWS|aws)[_\-]?ACCESS[_\-]?KEY[_\-]?ID[=:]\s*['\"]?(AKIA[0-9A-Z]{16})['\"]?"),
    "aws_secret_key": re.compile(r"(?:AWS|aws)[_\-]?SECRET[_\-]?(?:ACCESS)?[_\-]?KEY[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    "slack_token": re.compile(r"xox[baprs]-[0-9A-Za-z\-_]{10,}"),
    "private_key_pem": re.compile(r"-----BEGIN (?:RSA|DSA|EC|OPENSSH) PRIVATE KEY-----"),
    "jwt_secret": re.compile(r"(?:JWT|jwt)[_\-]?SECRET[=:]\s*['\"]?([A-Za-z0-9_\-!@#$%^&*]{8,})['\"]?"),
    "database_url": re.compile(r"(?:DATABASE_URL|DB_URL|MONGO_URI|REDIS_URL)[=:]\s*['\"]?(.+?)['\"]?(?:\s|$)"),
    "stripe_key": re.compile(r"(?:sk|pk)_(?:live|test)_[0-9A-Za-z]{24,}"),
    "github_pat": re.compile(r"gh[pousr]_[A-Za-z0-9_]{36}"),
    "gitlab_token": re.compile(r"glpat-[A-Za-z0-9\-_]{20,}"),
}

ENTROPY_THRESHOLD = 4.2


def calculate_entropy(data: str) -> float:
    if not data:
        return 0.0
    entropy = 0.0
    length = len(data)
    for byte in range(256):
        count = data.count(chr(byte))
        if count > 0:
            prob = count / length
            entropy -= prob * math.log2(prob)
    return entropy


def is_likely_secret(candidate: str) -> bool:
    if len(candidate) < 16 or len(candidate) > 512:
        return False
    return calculate_entropy(candidate) > ENTROPY_THRESHOLD


# ── Credential Classification ─────────────────────────────────────────────────

class CredentialClass(Enum):
    AWS_IAM = "aws_iam"
    AWS_SESSION = "aws_session"
    GCP_SA = "gcp_service_account"
    AZURE_SP = "azure_service_principal"
    GITHUB_PAT = "github_personal_access_token"
    GITLAB_TOKEN = "gitlab_access_token"
    JWT_SECRET = "jwt_signing_secret"
    API_KEY = "api_key"
    DATABASE_URL = "database_connection_string"
    PRIVATE_KEY = "private_key_pem"
    BASIC_AUTH = "basic_auth_credentials"
    UNKNOWN = "unknown"


CREDENTIAL_VALIDATORS = {
    CredentialClass.AWS_IAM: re.compile(r"^AKIA[0-9A-Z]{16}$"),
    CredentialClass.GITHUB_PAT: re.compile(r"^gh[pousr]_[A-Za-z0-9_]{36}$"),
    CredentialClass.GITLAB_TOKEN: re.compile(r"^glpat-[A-Za-z0-9\-_]{20,}$"),
    CredentialClass.PRIVATE_KEY: re.compile(r"^-----BEGIN (?:RSA|DSA|EC|OPENSSH) PRIVATE KEY-----"),
}

CREDENTIAL_RISK_LEVELS = {
    CredentialClass.AWS_IAM: 10, CredentialClass.AWS_SESSION: 9,
    CredentialClass.GCP_SA: 10, CredentialClass.AZURE_SP: 10,
    CredentialClass.GITHUB_PAT: 7, CredentialClass.JWT_SECRET: 8,
    CredentialClass.PRIVATE_KEY: 10, CredentialClass.DATABASE_URL: 9,
    CredentialClass.API_KEY: 6, CredentialClass.BASIC_AUTH: 4,
    CredentialClass.UNKNOWN: 1,
}


def classify_credential(value: str) -> CredentialClass:
    for cred_class, pattern in CREDENTIAL_VALIDATORS.items():
        if pattern.match(value):
            return cred_class
    return CredentialClass.UNKNOWN


def assess_credential_risk(cred_class: CredentialClass, context: str = None) -> int:
    base_score = CREDENTIAL_RISK_LEVELS.get(cred_class, 1)
    if context and any(word in context.lower() for word in ("prod", "production", "live", "admin")):
        base_score = min(10, base_score + 1)
    return base_score


# ── CVE-to-Attack Mapping ─────────────────────────────────────────────────────

CVE_ATTACK_MAP = {
    "sql_injection": {
        "cwe_ids": ["CWE-89"], "tools": ["sqlmap", "manual_payload"],
        "indicators": ["database error", "syntax error", "unexpected token"], "priority": "critical",
    },
    "xss": {
        "cwe_ids": ["CWE-79"], "tools": ["manual_payload", "browser_validation"],
        "indicators": ["<script> reflected", "javascript: URI"], "priority": "high",
    },
    "ssti": {
        "cwe_ids": ["CWE-94", "CWE-1336"], "tools": ["tplmap", "manual_payload"],
        "indicators": ["{{7*7}}", "{{config}}"], "priority": "critical",
    },
    "ssrf": {
        "cwe_ids": ["CWE-918"], "tools": ["collaborator_client", "manual_payload"],
        "indicators": ["internal IP response", "metadata endpoint"], "priority": "high",
    },
    "command_injection": {
        "cwe_ids": ["CWE-77", "CWE-78"], "tools": ["commix", "manual_payload"],
        "indicators": ["; id", "| whoami", "$(whoami)"], "priority": "critical",
    },
    "path_traversal": {
        "cwe_ids": ["CWE-22"], "tools": ["dotdotpwn", "manual_payload"],
        "indicators": ["../../../etc/passwd"], "priority": "high",
    },
    "jwt_attacks": {
        "cwe_ids": ["CWE-347"], "tools": ["jwt_tool", "manual_crafting"],
        "indicators": ["alg:none", "empty signature", "weak HMAC key"], "priority": "high",
    },
    "deserialization": {
        "cwe_ids": ["CWE-502"], "tools": ["ysoserial", "marshalsec"],
        "indicators": ["pickle", "java.io.ObjectInputStream"], "priority": "critical",
    },
}

SEVERITY_CVSS = {
    "critical": (9.0, 10.0), "high": (7.0, 8.9),
    "medium": (4.0, 6.9), "low": (0.1, 3.9), "info": (0.0, 0.0),
}

TECH_VULN_MAP = {
    "apache": {"2.4.49": "CVE-2021-41773", "2.4.50": "CVE-2021-42013"},
    "nginx": {"1.20.0": "CVE-2021-23017"},
    "tomcat": {"9.0.40": "CVE-2021-25122"},
    "struts2": {"2.5.26": "CVE-2020-17530"},
    "log4j": {"2.14.1": "CVE-2021-44228"},
    "spring": {"5.3.0": "CVE-2022-22965"},
}


def suggest_tools_for_cwe(cwe_id: str) -> list:
    for _category, info in CVE_ATTACK_MAP.items():
        if cwe_id in info.get("cwe_ids", []):
            return info.get("tools", [])
    return []
