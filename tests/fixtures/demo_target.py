"""Local deliberately-vulnerable fixture — swarm test target.

Binds 127.0.0.1:8099 ONLY. Intentional (safe, self-contained) flaws for
the swarm to find:
  1. /api/v2/users?role=  — role param taken from the query (auth bypass shape)
  2. /redirect?to=        — unvalidated open redirect
  3. /admin               — no auth check
"""
from fastapi import FastAPI, Query, Response
from fastapi.responses import RedirectResponse
import uvicorn

app = FastAPI(title="demo-target")

USERS = {
    "1": {"id": "1", "name": "admin", "role": "admin", "email": "admin@target.example"},
    "2": {"id": "2", "name": "dev", "role": "user", "email": "dev@target.example"},
    "3": {"id": "3", "name": "guest", "role": "user", "email": "guest@target.example"},
}


@app.get("/")
def root():
    return {"service": "demo-target", "endpoints": ["/api/v2/users", "/redirect", "/admin"]}


@app.get("/api/v2/users")
def users(role: str = Query(default="user")):
    # FLAW: role comes from the query — anyone can read any record set
    if role == "admin":
        return {"users": list(USERS.values())}
    return {"users": [u for u in USERS.values() if u["role"] == "user"]}


@app.get("/api/v2/users/{uid}")
def user(uid: str):
    return USERS.get(uid, Response(status_code=404))


@app.get("/redirect")
def redirect(to: str = Query(default="/")):
    # FLAW: open redirect — no host validation
    return RedirectResponse(to, status_code=302)


@app.get("/admin")
def admin():
    # FLAW: sensitive panel, no auth check at all
    return {"panel": "admin", "flag": "unauthenticated-access", "secrets": ["demo-only"]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")
