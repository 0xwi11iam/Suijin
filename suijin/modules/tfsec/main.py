from pathlib import Path

_TF_RULES = [
    ("public ingress", 'ingress {', 'cidr_blocks = ["0.0.0.0/0"]'),
    ("public egress", 'egress {', 'cidr_blocks = ["0.0.0.0/0"]'),
    ("open security group", "aws_security_group", "0.0.0.0/0"),
    ("plaintext secret", " = ", '"${var.'),
]
_TF_TEXT_RULES = [
    ("s3 no encryption", 'resource "aws_s3_bucket"', None),
    ("iam wildcard action", '"*"', "Action"),
    ("privileged container", "privileged *= *true", None),
    ("root user keys", "aws_iam_access_key", None),
]
_DF_RULES = [
    ("latest tag", "FROM .*:latest", "pin your image versions"),
    ("root user", "USER root", "run as non-root"),
    ("secrets in build", "(PASSWORD|TOKEN|SECRET)=", "use build args/secrets"),
    ("curl|bash pattern", r"curl .*\| *(ba)?sh", "verify checksums"),
    ("adds . ", "ADD . ", "use COPY (ADD fetches/extracts)"),
    ("exposed docker socket", "/var/run/docker.sock", "container escape vector"),
]


def _read(text: str, file: str):
    if file:
        p = Path(file).expanduser()
        if not p.is_file():
            return None, f"Error: {p} not found"
        return p.read_text(encoding="utf-8", errors="ignore"), None
    return text or "", None


def tf_scan(text: str = "", file: str = "") -> str:
    src, err = _read(text, file)
    if err or not src:
        return err or "Error: text or file required"
    import re
    findings = []
    for label, pat, _ in _TF_TEXT_RULES:
        n = len(re.findall(pat, src)) if label != "s3 no encryption" else src.count('resource "aws_s3_bucket"') - src.count("server_side_encryption")
        if n and n > 0:
            findings.append(f"{label} (~{max(n, 1)}x)")
    if "0.0.0.0/0" in src:
        findings.append("wildcard 0.0.0.0/0 present (check scope)")
    return "\n".join(f"- {f}" for f in findings) or "No insecure terraform patterns matched (deep review still advised)."


def dockerfile_scan(text: str = "", file: str = "") -> str:
    src, err = _read(text, file)
    if err or not src:
        return err or "Error: text or file required"
    import re
    findings = []
    for label, pat, fix in _DF_RULES:
        if re.search(pat, src, re.I):
            findings.append(f"- {label} ({fix})")
    if "USER " not in src:
        findings.append("- no USER directive (container runs as root)")
    return "\n".join(findings) or "Dockerfile looks clean against the common anti-pattern list."
