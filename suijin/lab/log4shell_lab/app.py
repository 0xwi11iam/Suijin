"""Log4Shell Lab — realistic Log4j-vulnerable application. Port 5903.

Flaws (multiple, not just Log4j):
  1. CVE-2021-44228 (Log4Shell): User-Agent, X-Api-Version, and
     request body are "logged" via the vulnerable pattern
  2. CVE-2021-45046: session cookie value also logged
  3. Sensitive data exposure: /actuator/config reveals the flag + env
  4. Path traversal in /static/ handler (../../etc/passwd)
  5. Information disclosure: verbose errors + stack traces
"""
import re
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request

app = Flask(__name__)
app.config["SECRET_KEY"] = "log4j_lab_super_secret_5903"

# Simulated "environment" the actuator leaks
ENV = {
    "LOG4J_VERSION": "2.14.1",
    "JAVA_VERSION": "11.0.13",
    "FLAG": "FLAG{log4shell_rce_via_jndi_5903}",
    "DATABASE_URL": "jdbc:postgresql://internal-db:5432/prod",
    "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
}

JNDI_PATTERN = re.compile(r"\$\{jndi:(ldap|rmi|dns|iiop|http)://[^}]+\}", re.I)
LOOKUP_PATTERN = re.compile(r"\$\{(?:env|sys|java|date|lower|upper):[^}]*\}", re.I)
TRAVERSAL_PATTERN = re.compile(r"\.\.[\\\\/]")

_access_log = []


def _check_log4j(value: str, source: str) -> str:
    """Simulate Log4j processing: if a JNDI lookup is in a logged field,
    the 'JVM' makes an outbound connection (which we record)."""
    if JNDI_PATTERN.search(value):
        _access_log.append({"source": source, "payload": value[:120], "ts": time.time(), "type": "jndi_lookup"})
        return f"[LOG4J EXPLOITED] {source} -> outbound connection initiated by log processing"
    if LOOKUP_PATTERN.search(value):
        _access_log.append({"source": source, "payload": value[:120], "ts": time.time(), "type": "lookup"})
        # Log4j lookup substitution: ${env:FLAG} actually resolves!
        m = re.search(r"\$\{env:(\w+)\}", value)
        if m and m.group(1) in ENV:
            leaked = ENV[m.group(1)]
            _access_log.append({"source": source, "leaked": leaked[:60], "ts": time.time(), "type": "env_leak"})
            return f"[LOG4J LOOKUP] {source} -> resolved: {leaked}"
    _access_log.append({"source": source, "payload": value[:120], "ts": time.time(), "type": "normal"})
    return ""


@app.before_request
def log_request():
    """Simulate Log4j logging multiple request fields."""
    ua = request.headers.get("User-Agent", "")
    _check_log4j(ua, "User-Agent")
    api_ver = request.headers.get("X-Api-Version", "")
    _check_log4j(api_ver, "X-Api-Version")
    cookie = request.headers.get("Cookie", "")
    _check_log4j(cookie, "Cookie")
    if request.is_json:
        body = request.get_data(as_text=True)[:2000]
        _check_log4j(body, "body")


@app.route("/")
def index():
    return jsonify(
        {
            "service": "Spring Boot API",
            "version": "2.6.1",
            "log4j": "2.14.1",
            "endpoints": ["/", "/api/users", "/api/search", "/actuator/health", "/actuator/config", "/static/<file>"],
        }
    )


@app.route("/api/users")
def users():
    users_list = [
        {"id": 1, "username": "admin", "email": "admin@corp.example", "role": "ADMIN"},
        {"id": 2, "username": "svc_backup", "email": "backup@corp.example", "role": "SERVICE"},
    ]
    q = request.args.get("q", "")
    # surface any UA-triggered JNDI that the before_request handler detected
    jndi_hits = [e for e in _access_log[-10:] if e.get("type") == "jndi_lookup"]
    extra = {}
    if q:
        result = _check_log4j(q, "search_query")
        if result:
            extra["log_note"] = result
    if jndi_hits:
        extra["log4j_note"] = f"JNDI lookup detected in {jndi_hits[0]['source']} — Log4j 2.14.1 processed it"
    return jsonify({"users": users_list, **extra})


@app.route("/api/search")
def search():
    q = request.args.get("q", "")
    result = _check_log4j(q, "search_endpoint")
    data = {"query": q, "results": [], "total": 0}
    if result:
        data["log4j_output"] = result
        data["note"] = "The search query was logged by Log4j version 2.14.1"
    return jsonify(data)


@app.route("/actuator/health")
def health():
    return jsonify({"status": "UP", "components": {"db": {"status": "UP"}, "diskSpace": {"status": "UP"}}})


@app.route("/actuator/config")
def actuator_config():
    """Exposed actuator endpoint leaking environment + secrets."""
    return jsonify({"propertySources": [{"name": "systemEnvironment", "properties": {k: {"value": v} for k, v in ENV.items()}}]})


@app.route("/actuator/env")
def actuator_env():
    """Alternative actuator path (same leak, different URL)."""
    return jsonify({k: v for k, v in ENV.items() if k != "FLAG"})


@app.route("/actuator/logfile")
def logfile():
    """Log file endpoint: shows what Log4j has been processing."""
    return jsonify({"entries": _access_log[-50:], "total": len(_access_log)})


@app.route("/static/<path:filename>")
def static_files(filename):
    """Path traversal vulnerable static file handler."""
    if TRAVERSAL_PATTERN.search(filename):
        # vulnerable: no normalization, just tries to read
        try:
            target = Path("/etc") / filename.replace("../../", "")
            if target.exists():
                return Response(target.read_text(errors="ignore"), mimetype="text/plain")
        except (OSError, ValueError):
            pass
    return jsonify({"error": "not found"}), 404


@app.errorhandler(Exception)
def handle_error(e):
    """Verbose errors leak stack traces."""
    return jsonify({"error": str(e), "type": type(e).__name__, "trace": True}), 500


if __name__ == "__main__":
    import os

    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5903")), debug=False)
