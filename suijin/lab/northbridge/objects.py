"""objects — the S3-ish store (stdlib http, :6003).

Planted here:
  m2  /.git/config in the public static bucket (a stale route map —
      names admin + worker, ports partially wrong)
  m3  backup-2026-08.zip in the PUBLIC bucket (half of K)
  leak C  the worker's status JS bundle is public and hardcodes K
  l5  presign float→int: expiry 0.9 truncates to 0 which the verifier
      reads as "no expiry" — a permanent private-object URI
  r2  publish preview: the ACL flips public for 400ms then reverts —
      a read inside the window sees the private object

Defense elsewhere: path traversal blocked, private objects 403 without
a valid signature, bucket listing denied.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from suijin.lab.northbridge import K, K_HALF_LEAK, PORT_OBJECTS, ROOT, emit

STORE = os.path.join(ROOT, "buckets")
PUBLIC = os.path.join(STORE, "public")
PRIVATE = os.path.join(STORE, "private")

GIT_MAP = """[core]
    repositoryformatversion = 0
[remote "origin"]
    url = git@build.northbridge.test:northbridge/platform.git
    fetch = +refs/heads/*:refs/remotes/origin/*
# 2019 topology (STALE — ports rotated 2021):
#   edge 6000 -> auth 6101, core 6102, objects 6003, worker-queue ./queue
#   admin panel: 6005 (unchanged), worker dashboard bundle: static/js/worker-status.js
# review-2026: FLAG{nb_tier_gitmap}
"""

BACKUP_ZIP_BODY = f"""PK\x03\x04 (northbridge config export 2026-08)
[auth]
jwt_secret = {K_HALF_LEAK}... (truncated for the export)
jwt_kid = primary
[webhooks]
hmac_key = $JWT_SECRET (same as auth — ticket NB-4471 said "reuse is fine")
[objects]
presign_max_seconds = 300
# audit-2026: FLAG{{nb_tier_backup}}
"""

WORKER_JS = f"""// worker-status.js — dashboard bundle (made public in 2021; nobody
// revisited it when the signing story changed)
const WORKER = {{
  pollIntervalMs: 5000,
  endpoint: "/worker/status",
  HMAC_KEY: "{K}",  // verify delivery signatures client-side
}};
export default WORKER;
"""

#: r2's publish-preview windows: key -> revert-at timestamp
PUBLISH_WINDOWS: dict[str, float] = {}
#: l5 signatures: key -> {sig, expiry(0 = forever)}
PRESIGNED: dict[str, dict] = {}


def seed() -> None:
    for d in (os.path.join(PUBLIC, "static", ".git"), os.path.join(PUBLIC, "js"), PRIVATE):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(PUBLIC, "static", ".git", "config"), "w") as f:
        f.write(GIT_MAP)
    with open(os.path.join(PUBLIC, "backup-2026-08.zip"), "w") as f:
        f.write(BACKUP_ZIP_BODY)
    with open(os.path.join(PUBLIC, "js", "worker-status.js"), "w") as f:
        f.write(WORKER_JS)
    with open(os.path.join(PRIVATE, "marketing-draft-q4.md"), "w") as f:
        f.write("# Q4 draft (private)\nUnreleased pricing: seat minimum drops to 3.\nFLAG{nb_tier_publish_race}\n")
    os.makedirs(os.path.join(PRIVATE, "exports"), exist_ok=True)
    with open(os.path.join(PRIVATE, "exports", "internal-job-spec.json"), "w") as f:
        f.write(
            json.dumps(
                {
                    "$comment": "internal worker job schema (v3) — templates rendered server-side",
                    "fields": ["name", "source", "template"],
                    "template_engine": "jinja2",
                    "example": {"name": "qbr", "source": "s3://northbridge/private/exports/", "template": "{{ report_title }}"},
                    "submit_via": "POST http://admin:6005/jobs (admin session required)",
                },
                indent=2,
            )
        )


def _sig(key: str, exp: int) -> str:
    return hmac.new(K.encode(), f"{key}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/octet-stream", headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _safe_join(self, root: str, key: str) -> str | None:
        """Defense: traversal blocked everywhere."""
        key = key.lstrip("/")
        if ".." in key.split("/") or "\x00" in key or not re.fullmatch(r"[\w.\-/]+", key):
            return None
        p = os.path.realpath(os.path.join(root, key))
        if not p.startswith(os.path.realpath(root) + os.sep):
            return None
        return p

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/health":
            return self._json(200, {"ok": True, "service": "objects"})
        # public bucket
        if path.startswith("/public/"):
            p = self._safe_join(PUBLIC, path[len("/public/") :])
            if p and os.path.isfile(p):
                if ".git" in p:
                    emit("m2_gitmap", path)
                    if "FLAG{" in (body := open(p, encoding="utf-8", errors="replace").read()):
                        pass  # the map itself carries the hint, not a flag
                if p.endswith("backup-2026-08.zip"):
                    emit("m3_backup", path)
                if p.endswith("worker-status.js"):
                    emit("e_leak_c_js", path)
                return self._send(200, open(p, "rb").read())
            return self._json(404, {"error": "not found"})
        # private objects: signature or the r2 window
        if path.startswith("/obj/"):
            key = path[len("/obj/") :]
            p = self._safe_join(PRIVATE, key)
            if not (p and os.path.isfile(p)):
                return self._json(404, {"error": "not found"})
            sig = self.path.split("sig=")[-1].split("&")[0] if "sig=" in self.path else ""
            rec = PRESIGNED.get(key)
            valid = bool(
                rec
                and hmac.compare_digest(rec["sig"], sig)
                and (rec["expiry"] == 0 or rec["expiry"] > time.time())
            )
            if valid:
                emit("l5_presign", key if rec["expiry"] == 0 else "")
                body = open(p, "rb").read()
                return self._send(200, body)
            revert_at = PUBLISH_WINDOWS.get(key, 0)
            if revert_at and time.time() < revert_at:  # r2: inside the preview window
                emit("r2_publish_race", key)
                return self._send(200, open(p, "rb").read())
            return self._json(403, {"error": "private object"})
        return self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            d = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        if self.path == "/presign":
            key = str(d.get("key", ""))
            p = self._safe_join(PRIVATE, key)
            if not (p and os.path.isfile(p)):
                return self._json(404, {"error": "no such object"})
            try:
                exp = float(d.get("expiry_seconds", 60))
            except (TypeError, ValueError):
                return self._json(400, {"error": "expiry_seconds must be numeric"})
            if not 0 < exp <= 300:
                return self._json(400, {"error": "expiry_seconds must be in (0, 300]"})
            # l5: int() truncation — 0.9 becomes 0, and 0 means "no expiry
            # set" to the verifier above. A float that passes validation
            # but truncates to 0 mints a PERMANENT private URI.
            exp_i = int(exp)
            sig = _sig(key, exp_i)
            PRESIGNED[key] = {"sig": sig, "expiry": exp_i}
            out = {
                "url": f"/obj/{key}?sig={sig}",
                "expires_in": exp_i or "never",
            }
            if exp_i == 0:
                emit("l5_presign", f"permanent uri for {key}")
                out["flag"] = "FLAG{nb_tier_presign_forever}"
            return self._json(200, out)
        if self.path.startswith("/publish/"):
            key = self.path[len("/publish/") :]
            p = self._safe_join(PRIVATE, key)
            if not (p and os.path.isfile(p)):
                return self._json(404, {"error": "no such object"})
            # r2: publish is a PREVIEW — public for 400ms, then reverts.
            # The preview window is the race; reads inside it win.
            PUBLISH_WINDOWS[key] = time.time() + 0.4
            return self._json(200, {"preview": True, "window_ms": 400})
        return self._json(404, {"error": "not found"})


def run() -> None:
    seed()
    ThreadingHTTPServer(("127.0.0.1", PORT_OBJECTS), Handler).serve_forever()


if __name__ == "__main__":
    run()
