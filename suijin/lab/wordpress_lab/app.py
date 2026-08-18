"""WordPress Lab — simulated WordPress with vulnerable plugins. Port 5904."""
from flask import Flask, request, jsonify
import xml.etree.ElementTree as ET

app = Flask(__name__)
users = {"admin": "wp_admin_pass_2024", "editor": "editor123", "subscriber": "sub123"}
posts = [{"id":1,"title":"Hello World","content":"Welcome to WordPress."},
         {"id":2,"title":"Secret Post","content":"FLAG{wp_secret_post_5904}","status":"private"}]

@app.route("/")
def index(): return "<h1>WordPress Site</h1><p>Just another WordPress site.</p><a href='/wp-admin'>Admin</a>"

@app.route("/xmlrpc.php", methods=["POST"])
def xmlrpc():
    data = request.data.decode()
    # User enumeration via wp.getUsersBlogs
    if "wp.getUsersBlogs" in data:
        for user in users:
            if f"<name>{user}</name>" in data or user in data:
                return f"<response><fault><value><string>Incorrect password for user '{user}'</string></value></fault></response>"
    # Authenticated RCE via plugin upload (simulated)
    if "admin" in data and "wp_admin_pass_2024" in data:
        return f"<response><param><value><string>Authenticated. FLAG{{wp_xmlrpc_auth_5904}}</string></value></param></response>"
    return "<response><fault><value><string>Invalid request</string></value></fault></response>"

@app.route("/wp-admin")
def admin():
    return "<h1>Admin Panel</h1><form method='POST' action='/wp-admin/login'><input name='user'><input name='pass' type='password'><button>Login</button></form>"

@app.route("/wp-admin/login", methods=["POST"])
def login():
    u = request.form.get("user","")
    p = request.form.get("pass","")
    if u in users and users[u] == p:
        return f"Login successful! FLAG{{wp_admin_access_5904}}<br>Posts: {json.dumps(posts)}"
    return "Login failed. Valid user: admin" if u != "admin" else "Wrong password for admin"

@app.route("/wp-content/plugins/vulnerable-plugin/config.php")
def plugin_leak():
    return "<?php\n$db_host = 'localhost';\n$db_user = 'wp_user';\n$db_pass = 'wp_db_pass_5904';\n$api_key = 'wp_api_key_5904';\n?>"

app.run(host="0.0.0.0", port=5904, debug=False)
