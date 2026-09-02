"""
Complex vulnerable application for blue team defense training.
Port 5906 — 25 endpoints across 8 route groups.

Authentication: JWT with refresh tokens, multiple roles.
Vulnerabilities: SQLi, IDOR, XSS, SSTI, XXE, command injection,
                 file upload bypass, JWT alg:none, CSRF, SSRF,
                 race condition, path traversal, mass assignment,
                 predictable tokens, info disclosure, CORS misconfig.
"""
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from functools import wraps

import jwt as pyjwt
from flask import Flask, Response, g, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app, supports_credentials=True, origins="*")
app.config['SECRET_KEY'] = 'super-secret-key-change-me-2024'
app.config['JWT_ALGORITHM'] = 'HS256'
DB = "/tmp/blue_defend.db"
TRAFFIC_LOG = "/tmp/blue_defend_traffic.jsonl"
UPLOAD_DIR = "/tmp/blue_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.before_request
def log_request():
    entry = {
        "timestamp": datetime.now().isoformat(),
        "method": request.method,
        "path": request.path,
        "query": dict(request.args),
        "body": request.get_data(as_text=True)[:1000],
        "ip": request.remote_addr,
        "user_agent": str(request.user_agent),
        "headers": {k: v for k, v in request.headers.items()
                    if k.lower() in ("content-type", "cookie", "authorization",
                                     "x-forwarded-for", "x-admin", "x-csrf-token",
                                     "origin", "referer")},
    }
    with open(TRAFFIC_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

# Tarpit check — reads /tmp/blue_tarpit.json written by blue team agent
# When the blue team flags an IP for DECEIVE, this sleeps the response
TARPIT_FILE = "/tmp/blue_tarpit.json"

@app.before_request
def check_tarpit():
    try:
        if os.path.exists(TARPIT_FILE):
            with open(TARPIT_FILE) as f:
                tarpit_state = json.loads(f.read())
            ip = request.remote_addr
            if ip in tarpit_state:
                delay = tarpit_state[ip].get("delay", 5.0)
                since = tarpit_state[ip].get("since", 0)
                elapsed = time.time() - since
                # Keep tarpit active for 30 minutes
                if elapsed < 1800:
                    time.sleep(min(delay, 15.0))
    except Exception:
        pass  # Never break the app if tarpit file is malformed

def init_db():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT,
        email TEXT, role TEXT DEFAULT 'user', api_key TEXT,
        created_at TEXT, reset_token TEXT, reset_expires TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY, user_id INTEGER, filename TEXT,
        filepath TEXT, content_type TEXT, size INTEGER,
        uploaded_at TEXT, is_public INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS coupons (
        id INTEGER PRIMARY KEY, code TEXT UNIQUE, value REAL,
        max_uses INTEGER, used_count INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY, value TEXT)""")
    conn.execute("INSERT OR IGNORE INTO users VALUES (1,'admin','8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918','admin@local','admin','sk-admin-0001',?,NULL,NULL)", (datetime.now().isoformat(),))
    conn.execute("INSERT OR IGNORE INTO users VALUES (2,'moderator','e150a1ec81e8e93e1eae2c3a77e66ec6dbd6a3b460f89c1d08aecf422ee401a0','mod@local','moderator',NULL,?,NULL,NULL)", (datetime.now().isoformat(),))
    conn.execute("INSERT OR IGNORE INTO users VALUES (3,'analyst','c51bce3f07e24753c7f0f2066f6d5de4e2c0a7660e8ca6c1ef61a17b48927bf5','analyst@local','analyst',NULL,?,NULL,NULL)", (datetime.now().isoformat(),))
    conn.execute("INSERT OR IGNORE INTO users VALUES (4,'user1','0a041b9462caa4a31bac3567e0b6e6fd9100787db2ab433d96f6d178cabfce90','user1@local','user',NULL,?,NULL,NULL)", (datetime.now().isoformat(),))
    conn.execute("INSERT OR IGNORE INTO users VALUES (5,'guest','ba3253876aed6bc22d4a6ff53d8406c6ad864195ed144ab5c87621b6c233b548','guest@local','guest',NULL,?,NULL,NULL)", (datetime.now().isoformat(),))
    conn.execute("INSERT OR IGNORE INTO documents VALUES (1,1,'secret_plans.pdf','/tmp/blue_uploads/secret_plans.pdf','application/pdf',1024,?,0)", (datetime.now().isoformat(),))
    conn.execute("INSERT OR IGNORE INTO documents VALUES (2,4,'notes.txt','/tmp/blue_uploads/notes.txt','text/plain',256,?,1)", (datetime.now().isoformat(),))
    conn.execute("INSERT OR IGNORE INTO coupons VALUES (1,'WELCOME50',50.0,100,0)")
    conn.execute("INSERT OR IGNORE INTO coupons VALUES (2,'ADMIN100',100.0,1,0)")
    conn.execute("INSERT OR IGNORE INTO config VALUES ('maintenance_mode','false')")
    conn.execute("INSERT OR IGNORE INTO config VALUES ('webhook_url','http://internal-api:8080/callback')")
    conn.commit()
    conn.close()

init_db()

def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def create_jwt(user_id: int, role: str, expires_hours: int = 24) -> str:
    payload = {"sub": user_id, "role": role, "iat": int(time.time()),
               "exp": int(time.time()) + expires_hours * 3600, "jti": uuid.uuid4().hex[:12]}
    return pyjwt.encode(payload, app.config['SECRET_KEY'], algorithm=app.config['JWT_ALGORITHM'])

def decode_jwt(token: str) -> dict:
    try: return pyjwt.decode(token, options={"verify_signature": False})
    except Exception:

        pass  # Lab test apppass
    try: return pyjwt.decode(token, app.config['SECRET_KEY'], algorithms=[app.config['JWT_ALGORITHM']])
    except Exception:

        pass  # Lab test appreturn {}

def require_role(*roles: str):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            g.user = None; g.role = "anonymous"
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                payload = decode_jwt(auth[7:])
                if payload.get("sub"): g.user = payload["sub"]; g.role = payload.get("role", "user")
            api_key = request.headers.get("X-API-Key", "")
            if not g.user and api_key:
                conn = sqlite3.connect(DB)
                u = conn.execute("SELECT id, role FROM users WHERE api_key=?", (api_key,)).fetchone()
                conn.close()
                if u: g.user = u[0]; g.role = u[1]
            if not g.user or (roles and g.role not in roles):
                return jsonify({"error": "Unauthorized"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator

_rate_limits: dict = {}
def check_rate_limit(ip: str, max_req: int = 120, window: int = 60) -> bool:
    now = time.time()
    if ip not in _rate_limits: _rate_limits[ip] = []
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < window]
    _rate_limits[ip].append(now)
    return len(_rate_limits[ip]) <= max_req

@app.before_request
def enforce_rate_limit():
    if not check_rate_limit(request.remote_addr):
        return jsonify({"error": "Rate limit exceeded"}), 429

# ═══════════ ROUTES ═══════════

@app.route("/")
def index():
    return jsonify({"app": "BlueDefend Enterprise v3.1", "status": "operational",
        "endpoints": ["POST /auth/register","POST /auth/login","POST /auth/refresh",
        "GET /auth/me","PUT /auth/me","POST /auth/reset-password",
        "GET /auth/reset-password/<token>","GET /api/users","GET /api/users/<id>",
        "DELETE /api/users/<id>","POST /api/search","GET /api/documents",
        "POST /api/documents","GET /api/documents/<id>","GET /api/documents/<id>/download",
        "POST /api/export","GET /api/templates/<name>","POST /api/execute",
        "POST /api/coupons/redeem","GET /graphql","POST /graphql",
        "GET /admin","POST /admin/config","GET /health"], "docs": "/graphql"})

@app.route("/auth/register", methods=["POST"])
def register():
    data = request.get_json(force=True, silent=True) or {}
    username, password, email = data.get("username",""), data.get("password",""), data.get("email","")
    role = data.get("role", "user")  # MASS ASSIGNMENT
    if not username or not password: return jsonify({"error": "Username and password required"}), 400
    conn = sqlite3.connect(DB)
    try:
        conn.execute("INSERT INTO users (username,password,email,role,created_at) VALUES (?,?,?,?,?)",
                     (username, hash_password(password), email, role, datetime.now().isoformat()))
        conn.commit()
        uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        token = create_jwt(uid, role)
        return jsonify({"token": token, "user_id": uid, "role": role})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username already exists"}), 409
    finally: conn.close()

@app.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json(force=True, silent=True) or {}
    username, password = data.get("username",""), data.get("password","")
    conn = sqlite3.connect(DB)
    # VULNERABLE: SQL injection
    query = f"SELECT id, username, role, password FROM users WHERE username='{username}'"
    try: user = conn.execute(query).fetchone()
    except sqlite3.OperationalError as e: conn.close(); return jsonify({"error":"Database error","detail":str(e)}), 500
    conn.close()
    if user and hash_password(password) == user[3]:
        token = create_jwt(user[0], user[2])
        return jsonify({"token":token,"user_id":user[0],"username":user[1],"role":user[2]})
    return jsonify({"error":"Invalid credentials"}), 401

@app.route("/auth/refresh", methods=["POST"])
def refresh():
    auth = request.headers.get("Authorization","")
    if not auth.startswith("Bearer "): return jsonify({"error":"No token"}), 401
    payload = decode_jwt(auth[7:])
    if not payload.get("sub"): return jsonify({"error":"Invalid token"}), 401
    return jsonify({"token": create_jwt(payload["sub"], payload.get("role","user"))})

@app.route("/auth/me", methods=["GET"])
@require_role()
def auth_me():
    conn = sqlite3.connect(DB)
    u = conn.execute("SELECT id,username,email,role,api_key FROM users WHERE id=?",(g.user,)).fetchone()
    conn.close()
    return jsonify({"id":u[0],"username":u[1],"email":u[2],"role":u[3],"api_key":u[4]}) if u else (jsonify({"error":"Not found"}),404)

@app.route("/auth/me", methods=["PUT"])
@require_role()
def update_me():
    data = request.get_json(force=True, silent=True) or {}
    fields, values = [], []
    for key in ["email","role","password"]:
        if key in data:
            fields.append(f"{key}=?")
            values.append(hash_password(data[key]) if key=="password" else data[key])
    if not fields: return jsonify({"error":"No fields"}), 400
    values.append(g.user)
    conn = sqlite3.connect(DB)
    conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=?", values)
    conn.commit(); conn.close()
    return jsonify({"status":"updated"})

@app.route("/auth/reset-password", methods=["POST"])
def request_reset():
    data = request.get_json(force=True, silent=True) or {}
    email = data.get("email","")
    conn = sqlite3.connect(DB)
    u = conn.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone()
    if u:
        token = hashlib.md5(f"{email}{int(time.time())}".encode()).hexdigest()[:16]
        expires = (datetime.now()+timedelta(hours=1)).isoformat()
        conn.execute("UPDATE users SET reset_token=?,reset_expires=? WHERE id=?",(token,expires,u[0]))
        conn.commit(); conn.close()
        return jsonify({"message":"Reset link sent","debug_token":token})
    conn.close()
    return jsonify({"error":"Email not found"}), 404

@app.route("/auth/reset-password/<token>", methods=["GET"])
def validate_token(token):
    conn = sqlite3.connect(DB)
    u = conn.execute("SELECT id FROM users WHERE reset_token=? AND reset_expires>?",(token,datetime.now().isoformat())).fetchone()
    conn.close()
    return jsonify({"valid":True,"user_id":u[0]}) if u else (jsonify({"valid":False}),404)

@app.route("/api/users", methods=["GET"])
@require_role("admin","moderator","analyst")
def list_users():
    conn = sqlite3.connect(DB)
    users = conn.execute("SELECT id,username,email,role FROM users").fetchall()
    conn.close()
    return jsonify([{"id":u[0],"username":u[1],"email":u[2],"role":u[3]} for u in users])

@app.route("/api/users/<int:uid>", methods=["GET"])
@require_role()
def get_user(uid):
    conn = sqlite3.connect(DB)  # IDOR
    u = conn.execute("SELECT id,username,email,role,api_key FROM users WHERE id=?",(uid,)).fetchone()
    conn.close()
    return jsonify({"id":u[0],"username":u[1],"email":u[2],"role":u[3],"api_key":u[4]}) if u else (jsonify({"error":"Not found"}),404)

@app.route("/api/users/<int:uid>", methods=["DELETE"])
@require_role("admin")
def delete_user(uid):
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM users WHERE id=?",(uid,)); conn.commit(); conn.close()
    return jsonify({"status":"deleted"})

@app.route("/api/search", methods=["POST"])
@require_role()
def search():
    data = request.get_json(force=True, silent=True) or {}
    query, field = data.get("q",""), data.get("field","username")
    conn = sqlite3.connect(DB)
    sql = f"SELECT id,username,email,role FROM users WHERE {field} LIKE '%{query}%'"
    try: results = conn.execute(sql).fetchall()
    except sqlite3.OperationalError as e: conn.close(); return jsonify({"error":"Search error","detail":str(e)}), 500
    conn.close()
    return jsonify([{"id":r[0],"username":r[1],"email":r[2],"role":r[3]} for r in results])

@app.route("/api/documents", methods=["GET"])
@require_role()
def list_documents():
    conn = sqlite3.connect(DB)
    if g.role=="admin":
        docs = conn.execute("SELECT id,user_id,filename,content_type,size,uploaded_at,is_public FROM documents").fetchall()
    else:
        docs = conn.execute("SELECT id,user_id,filename,content_type,size,uploaded_at,is_public FROM documents WHERE user_id=? OR is_public=1",(g.user,)).fetchall()
    conn.close()
    return jsonify([{"id":d[0],"user_id":d[1],"filename":d[2],"content_type":d[3],"size":d[4],"uploaded_at":d[5],"is_public":d[6]} for d in docs])

@app.route("/api/documents", methods=["POST"])
@require_role()
def upload_document():
    if "file" not in request.files: return jsonify({"error":"No file"}), 400
    file = request.files["file"]
    if not file.filename: return jsonify({"error":"Empty filename"}), 400
    filename = file.filename
    filepath = os.path.join(UPLOAD_DIR, filename)
    file.save(filepath)
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO documents (user_id,filename,filepath,content_type,size,uploaded_at,is_public) VALUES (?,?,?,?,?,?,?)",
                 (g.user,filename,filepath,file.content_type or "application/octet-stream",os.path.getsize(filepath),datetime.now().isoformat(),0))
    conn.commit(); doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]; conn.close()
    return jsonify({"id":doc_id,"filename":filename,"status":"uploaded"})

@app.route("/api/documents/<int:doc_id>", methods=["GET"])
@require_role()
def get_document(doc_id):
    conn = sqlite3.connect(DB)  # IDOR
    doc = conn.execute("SELECT id,user_id,filename,filepath,content_type,size,is_public FROM documents WHERE id=?",(doc_id,)).fetchone()
    conn.close()
    return jsonify({"id":doc[0],"user_id":doc[1],"filename":doc[2],"content_type":doc[4],"size":doc[5],"is_public":doc[6]}) if doc else (jsonify({"error":"Not found"}),404)

@app.route("/api/documents/<int:doc_id>/download", methods=["GET"])
@require_role()
def download_document(doc_id):
    conn = sqlite3.connect(DB)
    doc = conn.execute("SELECT filepath,filename FROM documents WHERE id=?",(doc_id,)).fetchone()
    conn.close()
    if not doc: return jsonify({"error":"Not found"}), 404
    requested_path = request.args.get("path", doc[0])  # PATH TRAVERSAL
    full_path = os.path.join(UPLOAD_DIR, requested_path)
    try:
        with open(full_path,"rb") as f: content = f.read()
        return Response(content, mimetype="application/octet-stream", headers={"Content-Disposition":f"attachment; filename={doc[1]}"})
    except FileNotFoundError: return jsonify({"error":"File not found","path":full_path}), 404

@app.route("/api/export", methods=["POST"])
@require_role("admin","moderator")
def export_xml():
    xml_data = request.get_data(as_text=True)
    if not xml_data: return jsonify({"error":"No XML"}), 400
    try:  # XXE
        root = ET.fromstring(xml_data)
        result = {"export_type":root.tag,"items":[{"tag":c.tag,"text":c.text} for c in root]}
        return jsonify(result)
    except ET.ParseError as e: return jsonify({"error":"XML parse error","detail":str(e)}), 400

@app.route("/api/templates/<name>", methods=["GET"])
def render_template(name):
    template = request.args.get("data", f"Welcome to {name}")
    def simple_render(tmpl):
        result = tmpl
        for match in re.finditer(r'\{\{(.+?)\}\}', tmpl):
            expr = match.group(1).strip()
            try: val = eval(expr); result = result.replace(match.group(0), str(val))
            except Exception as e: result = result.replace(match.group(0), f"[Error:{e}]")
        return result
    return f"<html><body><h1>Template: {name}</h1><pre>{simple_render(template)}</pre></body></html>"

@app.route("/api/execute", methods=["POST"])
@require_role("admin")
def execute_command():
    data = request.get_json(force=True, silent=True) or {}
    command = data.get("command","echo hello")  # COMMAND INJECTION
    import subprocess
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=10)
        return jsonify({"stdout":result.stdout,"stderr":result.stderr,"exit_code":result.returncode})
    except subprocess.TimeoutExpired: return jsonify({"error":"Timeout"}), 408
    except Exception as e: return jsonify({"error":str(e)}), 500

@app.route("/api/coupons/redeem", methods=["POST"])
@require_role()
def redeem_coupon():
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code","")
    conn = sqlite3.connect(DB)
    coupon = conn.execute("SELECT id,value,max_uses,used_count FROM coupons WHERE code=?",(code,)).fetchone()
    if not coupon: conn.close(); return jsonify({"error":"Invalid coupon"}), 404
    cid, value, max_uses, used = coupon
    if used >= max_uses: conn.close(); return jsonify({"error":"Coupon exhausted"}), 400
    time.sleep(0.5)  # RACE CONDITION window
    conn.execute("UPDATE coupons SET used_count=used_count+1 WHERE id=?",(cid,))
    conn.commit(); conn.close()
    return jsonify({"status":"redeemed","value":value,"code":code})

@app.route("/graphql", methods=["GET","POST"])
def graphql():
    if request.method == "GET":
        return jsonify({"__schema":{"queryType":{"name":"Query"},"mutationType":{"name":"Mutation"},
            "types":[{"name":"User","fields":[{"name":"id","type":{"name":"Int"}},{"name":"username","type":{"name":"String"}},
            {"name":"email","type":{"name":"String"}},{"name":"role","type":{"name":"String"}},
            {"name":"apiKey","type":{"name":"String"}},{"name":"password","type":{"name":"String"}}]},
            {"name":"Document","fields":[{"name":"id","type":{"name":"Int"}},{"name":"filename","type":{"name":"String"}},
            {"name":"filepath","type":{"name":"String"}}]},
            {"name":"Config","fields":[{"name":"key","type":{"name":"String"}},{"name":"value","type":{"name":"String"}}]}]}})
    data = request.get_json(force=True, silent=True) or {}
    query_str = data.get("query","")
    conn = sqlite3.connect(DB)
    if "users" in query_str.lower():
        users = conn.execute("SELECT id,username,email,role FROM users").fetchall()
        conn.close(); return jsonify({"data":{"users":[{"id":u[0],"username":u[1],"email":u[2],"role":u[3]} for u in users]}})
    if "documents" in query_str.lower():
        docs = conn.execute("SELECT id,filename FROM documents").fetchall()
        conn.close(); return jsonify({"data":{"documents":[{"id":d[0],"filename":d[1]} for d in docs]}})
    if "config" in query_str.lower():
        cfgs = conn.execute("SELECT key,value FROM config").fetchall()
        conn.close(); return jsonify({"data":{"config":[{"key":c[0],"value":c[1]} for c in cfgs]}})
    conn.close(); return jsonify({"data":None,"errors":[{"message":"Unknown query"}]})

@app.route("/admin", methods=["GET"])
def admin_panel():
    if request.headers.get("X-Admin")=="true":  # AUTH BYPASS
        conn = sqlite3.connect(DB)
        users = conn.execute("SELECT id,username,role FROM users").fetchall()
        cfgs = conn.execute("SELECT key,value FROM config").fetchall()
        conn.close()
        return jsonify({"panel":"Admin Dashboard","flag":"FLAG{blue_defended_5906_admin_bypass}",
            "users":[{"id":u[0],"username":u[1],"role":u[2]} for u in users],
            "config":{c[0]:c[1] for c in cfgs}})
    return jsonify({"error":"Access denied"}), 403

@app.route("/admin/config", methods=["POST"])
@require_role("admin")
def update_config():
    data = request.get_json(force=True, silent=True) or {}
    key, value = data.get("key",""), data.get("value","")
    if key=="webhook_url" and value:  # SSRF
        import urllib.request
        try: resp = urllib.request.urlopen(value, timeout=5); actual = resp.read().decode()[:200]
        except Exception as e: actual = f"SSRF probe: {e}"
        conn = sqlite3.connect(DB)
        conn.execute("INSERT OR REPLACE INTO config (key,value) VALUES (?,?)",(key,value))
        conn.commit(); conn.close()
        return jsonify({"status":"updated","webhook_response":actual})
    conn = sqlite3.connect(DB)
    conn.execute("INSERT OR REPLACE INTO config (key,value) VALUES (?,?)",(key,value))
    conn.commit(); conn.close()
    return jsonify({"status":"updated"})

@app.route("/health", methods=["GET"])
def health():
    import platform
    import sys
    return jsonify({"status":"healthy","uptime":time.time(),"python":sys.version,
        "platform":platform.platform(),"node":platform.node(),
        "db_size":os.path.getsize(DB) if os.path.exists(DB) else 0,
        "upload_dir":UPLOAD_DIR,"rate_limited_ips":len(_rate_limits),
        "env_vars":{k:v for k,v in os.environ.items() if k.lower() in ("path","home","user","shell","pwd","lang")}})

# Information disclosure — leaks internal state, rate limits, tarpit config
@app.route("/debug/state", methods=["GET"])
def debug_state():
    tarpit_state = {}
    if os.path.exists(TARPIT_FILE):
        try: tarpit_state = json.loads(open(TARPIT_FILE).read())
        except Exception:

            pass  # Lab test apppass
    return jsonify({
        "db_path": DB, "upload_dir": UPLOAD_DIR,
        "traffic_log": TRAFFIC_LOG, "tarpit_file": TARPIT_FILE,
        "rate_limits": {k: len(v) for k, v in _rate_limits.items()},
        "tarpit_active": list(tarpit_state.keys()),
        "secret_key": app.config.get("SECRET_KEY", "")[:8] + "...",
        "jwt_algorithm": app.config.get("JWT_ALGORITHM", ""),
    })

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(__import__("os").environ.get("PORT", "5906")), debug=False)
