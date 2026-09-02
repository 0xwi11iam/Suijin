"""API-Only Lab — REST + GraphQL with BOLA, mass assignment, rate limit bypass. Port 5901."""

from flask import Flask, jsonify, request

app = Flask(__name__)
users = {1: {"id":1,"name":"Admin","email":"admin@corp.com","role":"admin","api_key":"ak_admin_5901"},
         2: {"id":2,"name":"Alice","email":"alice@corp.com","role":"user","api_key":"ak_alice_5901"},
         3: {"id":3,"name":"Bob","email":"bob@corp.com","role":"user","api_key":"ak_bob_5901"}}
rate_limits = {}

@app.route("/api/users")
def list_users(): return jsonify(list(users.values()))  # No auth check

@app.route("/api/users/<int:uid>")
def get_user(uid):
    # IDOR: no authorization — any user can view any other user
    return jsonify(users.get(uid, {"error":"not found"}))

@app.route("/api/users/<int:uid>", methods=["PUT"])
def update_user(uid):
    data = request.get_json(force=True) or {}
    if uid in users:
        users[uid].update(data)  # Mass assignment vulnerability
        return jsonify(users[uid])
    return jsonify({"error":"not found"}), 404

@app.route("/api/admin/flag")
def admin_flag():
    key = request.headers.get("X-API-Key", "")
    if key == "ak_admin_5901": return "FLAG{api_admin_flag_5901}"
    return jsonify({"error":"unauthorized"}), 401

@app.route("/graphql", methods=["POST"])
def graphql():
    query = request.get_json(force=True).get("query","")
    if "users" in query.lower():
        return jsonify({"data":{"users":list(users.values())}})  # No auth, introspection enabled
    if "flag" in query.lower(): return jsonify({"data":{"flag":"FLAG{graphql_introspection_5901}"}})
    return jsonify({"data":None})

@app.route("/api/health")
def health():
    # Rate limit bypass: no rate limiting on health endpoint
    return jsonify({"status":"ok", "internal": True, "db_password": "health_db_pass_5901"})

app.run(host="0.0.0.0", port=5901, debug=False)
