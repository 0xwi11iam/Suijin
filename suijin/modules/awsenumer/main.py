"""AWS enumeration — real service enumeration via the aws CLI.

v5.2: was a single sts get-caller-identity call (plus an unreachable
NameError branch — deleted). Now enumerates: identity, S3 buckets (+
readable check), IAM roles/users, EC2 instances, Secrets Manager
names, and Lambda functions. Every call is a real CLI invocation;
missing CLI or expired creds surface as clear errors.
"""

import json
import subprocess

_TIMEOUT = 60


def _aws(service, *args):
    """Run an aws CLI command, return parsed JSON or error string."""
    argv = ["aws", service, *args, "--output", "json", "--no-cli-pager"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT)
    except FileNotFoundError:
        return None, "Error: aws CLI not installed"
    except subprocess.TimeoutExpired:
        return None, f"Error: aws {service} timed out ({_TIMEOUT}s)"
    if r.returncode != 0:
        err = (r.stderr or "").strip().split("\n")[0][:200]
        return None, f"Error: aws {service} rc={r.returncode}: {err}"
    if not r.stdout.strip():
        return {}, ""
    try:
        return json.loads(r.stdout), ""
    except ValueError:
        return None, f"Error: aws {service} returned non-JSON"


def aws_identity():
    """Who am I? (account, ARN, user)"""
    data, err = _aws("sts", "get-caller-identity")
    if err:
        return err
    return f"Account: {data.get('Account', '?')}\nARN: {data.get('Arn', '?')}\nUser: {data.get('UserId', '?')[:20]}..."


def aws_s3():
    """List all S3 buckets + check public-read on each (sampled)."""
    data, err = _aws("s3api", "list-buckets", "--query", "Buckets[].Name")
    if err:
        return err
    buckets = data if isinstance(data, list) else []
    if not buckets:
        return "No S3 buckets (or list permission denied)"
    lines = [f"{len(buckets)} S3 bucket(s):"]
    for b in buckets:
        lines.append(f"  {b}")
        # sample ACL check on first 10
        if len(buckets) <= 10:
            acl, _ = _aws("s3api", "get-bucket-acl", "--bucket", b)
            if isinstance(acl, dict):
                for grant in acl.get("Grants", []):
                    grantee = grant.get("Grantee", {})
                    if grantee.get("URI") == "http://acs.amazonaws.com/groups/global/AllUsers":
                        perm = grant.get("Permission", "")
                        if perm in ("READ", "FULL_CONTROL"):
                            lines[-1] = f"  {b}  [PUBLIC-{perm}]"
    return "\n".join(lines)


def aws_iam():
    """List IAM roles and users."""
    roles, err1 = _aws("iam", "list-roles", "--query", "Roles[].RoleName")
    users, err2 = _aws("iam", "list-users", "--query", "Users[].UserName")
    if err1 and err2:
        return err1
    lines = []
    if not err1:
        rlist = roles if isinstance(roles, list) else []
        lines.append(f"IAM roles ({len(rlist)}):")
        for r in sorted(rlist)[:20]:
            lines.append(f"  {r}")
    if not err2:
        ulist = users if isinstance(users, list) else []
        lines.append(f"IAM users ({len(ulist)}):")
        for u in sorted(ulist)[:20]:
            lines.append(f"  {u}")
    return "\n".join(lines) or "No IAM data accessible"


def aws_ec2():
    """List EC2 instances across the default region."""
    data, err = _aws(
        "ec2",
        "describe-instances",
        "--query",
        "Reservations[].Instances[].{ID:InstanceId,Type:InstanceType,State:State.Name,IP:PrivateIpAddress}",
    )
    if err:
        return err
    instances = data if isinstance(data, list) else []
    if not instances:
        return "No EC2 instances in default region (or permission denied)"
    lines = [f"{len(instances)} EC2 instance(s):"]
    for i in instances[:20]:
        lines.append(f"  {i.get('ID', '?')}  {i.get('Type', '?')}  {i.get('State', '?')}  {i.get('IP', '-')}")
    return "\n".join(lines)


def aws_secrets():
    """List Secrets Manager secret names (values NOT read — names only)."""
    data, err = _aws("secretsmanager", "list-secrets", "--query", "SecretList[].Name")
    if err:
        return err
    secrets = data if isinstance(data, list) else []
    if not secrets:
        return "No secrets (or permission denied)"
    lines = [f"{len(secrets)} secret(s) (names only):"]
    for s in sorted(secrets)[:20]:
        lines.append(f"  {s}")
    return "\n".join(lines)


def aws_lambda():
    """List Lambda functions."""
    data, err = _aws("lambda", "list-functions", "--query", "Functions[].{Name:FunctionName,Runtime:Runtime,Role:Role}")
    if err:
        return err
    functions = data if isinstance(data, list) else []
    if not functions:
        return "No Lambda functions (or permission denied)"
    lines = [f"{len(functions)} Lambda function(s):"]
    for f in functions[:20]:
        role = str(f.get("Role", "")).split("/")[-1]
        lines.append(f"  {f.get('Name', '?')}  {f.get('Runtime', '?')}  role={role}")
    return "\n".join(lines)


def aws_enum(full: str = "true"):
    """Run full enumeration: identity, S3, IAM, EC2, Secrets, Lambda."""
    do_full = (full or "true").lower().startswith("t")
    sections = [
        ("IDENTITY", aws_identity),
        ("S3", aws_s3),
    ]
    if do_full:
        sections += [
            ("IAM", aws_iam),
            ("EC2", aws_ec2),
            ("SECRETS", aws_secrets),
            ("LAMBDA", aws_lambda),
        ]
    out = []
    for name, fn in sections:
        out.append(f"== {name} ==")
        out.append(fn())
        out.append("")
    return "\n".join(out).strip()
