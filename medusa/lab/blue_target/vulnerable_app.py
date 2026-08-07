"""Deliberately vulnerable app for blue team to defend. Port 5906."""
from flask import Flask, request, jsonify
import sqlite3, os
app = Flask(__name__)
DB = "/tmp/blue_defend.db"
def init_db():
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, password TEXT, role TEXT)")
    conn.execute("INSERT OR IGNORE INTO users VALUES (1,'admin','admin123','admin')")
    conn.execute("INSERT OR IGNORE INTO users VALUES (2,'user','user123','user')")
    conn.commit(); conn.close()
init_db()
@app.route("/")
def index(): return "<h1>Vulnerable App</h1><a href='/login'>Login</a> | <a href='/api/users'>API</a> | <a href='/admin'>Admin</a>"
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "GET": return "<form method='POST'><input name='user'><input name='pass' type='password'><button>Login</button></form>"
    user = request.form.get("user",""); pw = request.form.get("pass","")
    conn = sqlite3.connect(DB)
    result = conn.execute(f"SELECT * FROM users WHERE username='{user}' AND password='{pw}'").fetchone()
    conn.close()
    return f"Welcome {result[1]}! Role: {result[3]}" if result else "Login failed"
@app.route("/api/users")
def api_users():
    conn = sqlite3.connect(DB)
    users = conn.execute("SELECT id, username, role FROM users").fetchall()
    conn.close()
    return jsonify([{"id":u[0],"username":u[1],"role":u[2]} for u in users])
@app.route("/api/users/<int:uid>")
def api_user(uid):
    conn = sqlite3.connect(DB)
    u = conn.execute(f"SELECT id, username, role FROM users WHERE id={uid}").fetchone()
    conn.close()
    return jsonify({"id":u[0],"username":u[1],"role":u[2]}) if u else ("Not found",404)
@app.route("/admin")
def admin():
    return "<h1>Admin Panel</h1><p>Flag: FLAG{blue_defended_5906}</p>" if request.headers.get("X-Admin","")=="true" else "Access denied"
@app.route("/reset-password", methods=["POST"])
def reset():
    email = request.form.get("email","")
    return f"Password reset link sent to {email}" if email else "Email required"
if __name__ == "__main__": app.run(host="0.0.0.0", port=5906, debug=False)
