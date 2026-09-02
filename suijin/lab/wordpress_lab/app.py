"""WordPress Lab — realistic WordPress simulation with vulnerable plugins. Port 5904.

Flaws:
  1. SQL injection in search + user login (wp-sessions plugin)
  2. Stored XSS in comment content (no sanitization on display)
  3. Path traversal in media download (../../wp-config.php)
  4. XXE in WordPress XML-RPC pingback
  5. Unauthenticated user enumeration (wp-json/wp/v2/users)
  6. Plugin version disclosure (readme.txt accessible)
  7. Insecure file upload (no extension check)
  8. CSRF (no nonce on password change)
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

app = Flask(__name__)

DB = "/tmp/wordpress_lab.db"


def _db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            password_hash TEXT,
            email TEXT,
            role TEXT
        );
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY,
            title TEXT,
            content TEXT,
            status TEXT DEFAULT 'publish',
            author_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY,
            post_id INTEGER,
            author TEXT,
            content TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS options (
            option_name TEXT PRIMARY KEY,
            option_value TEXT
        );
    """)
    c.execute("INSERT OR IGNORE INTO users VALUES (1,'admin','$P$Bwp_admin_pass','admin@wp.example','administrator')")
    c.execute("INSERT OR IGNORE INTO users VALUES (2,'editor','$P$Beditor123','editor@wp.example','editor')")
    c.execute("INSERT OR IGNORE INTO users VALUES (3,'subscriber','$P$Bsub123','sub@wp.example','subscriber')")
    c.execute("INSERT OR IGNORE INTO posts VALUES (1,'Hello World','Welcome to WordPress.','publish',1)")
    c.execute("INSERT OR IGNORE INTO posts VALUES (2,'Secret Post','FLAG{wp_secret_post_5904}','private',1)")
    c.execute("INSERT OR IGNORE INTO posts VALUES (3,'Draft','Internal draft content','draft',2)")
    c.execute("INSERT OR IGNORE INTO options VALUES ('siteurl','http://wp.example')")
    c.execute("INSERT OR IGNORE INTO options VALUES ('wp_plugin_versions','wp-file-manager:6.9,wp-sessions:1.4')")
    conn.commit()
    conn.close()


_init_db()


@app.route("/")
def index():
    return jsonify(
        {
            "name": "WordPress 6.4.2",
            "plugins": {"wp-file-manager": "6.9 (vulnerable)", "wp-sessions": "1.4 (vulnerable)"},
            "endpoints": [
                "/wp-json/wp/v2/posts",
                "/wp-json/wp/v2/users",
                "/wp-login.php",
                "/wp-comments-post.php",
                "/wp-admin/admin-ajax.php",
                "/xmlrpc.php",
                "/wp-content/plugins/wp-file-manager/readme.txt",
                "/wp-content/uploads/",
            ],
        }
    )


# ── User enumeration (flaw 5) ──────────────────────────────────────


@app.route("/wp-json/wp/v2/users")
def wp_users():
    conn = _db()
    users = conn.execute("SELECT id, username, email, role FROM users").fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])


# ── SQL injection (flaw 1) ──────────────────────────────────────────


@app.route("/wp-json/wp/v2/posts")
def wp_posts():
    search = request.args.get("search", "")
    conn = _db()
    if search:
        # VULNERABLE: direct string interpolation
        query = f"SELECT * FROM posts WHERE title LIKE '%{search}%' OR content LIKE '%{search}%'"
    else:
        query = "SELECT * FROM posts WHERE status = 'publish'"
    try:
        rows = conn.execute(query).fetchall()
    except sqlite3.OperationalError as e:
        return jsonify({"error": str(e), "query": query}), 500
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/wp-login.php", methods=["GET", "POST"])
def wp_login():
    if request.method == "POST":
        data = request.json if request.is_json else request.form
        username = data.get("log", "")
        password = data.get("pwd", "")
        conn = _db()
        # VULNERABLE: SQL injection in login
        query = f"SELECT * FROM users WHERE username='{username}' AND password_hash LIKE '%{password}%'"
        try:
            row = conn.execute(query).fetchone()
        except sqlite3.OperationalError as e:
            conn.close()
            return jsonify({"error": str(e)}), 500
        conn.close()
        if row:
            return jsonify({"logged_in": True, "user": dict(row), "flag": "FLAG{wp_sqli_login_bypass_5904}"})
        return jsonify({"logged_in": False, "error": "Invalid credentials"})
    return jsonify({"form": "wp-login", "fields": ["log", "pwd"]})


# ── Stored XSS (flaw 2) ────────────────────────────────────────────


@app.route("/wp-comments-post.php", methods=["POST"])
def wp_comment():
    data = request.json if request.is_json else request.form
    post_id = data.get("comment_post_ID", 1)
    author = data.get("author", "anonymous")
    content = data.get("comment", "")
    conn = _db()
    # VULNERABLE: no sanitization of content
    conn.execute("INSERT INTO comments (post_id, author, content, created_at) VALUES (?,?,?,?)",
                 (int(post_id), author, content, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    comments = conn.execute("SELECT * FROM comments WHERE post_id=?", (int(post_id),)).fetchall()
    conn.close()
    return jsonify({"success": True, "comments": [dict(c) for c in comments]})


@app.route("/wp-json/wp/v2/comments")
def wp_get_comments():
    conn = _db()
    comments = conn.execute("SELECT * FROM comments ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    return jsonify([dict(c) for c in comments])


# ── Path traversal (flaw 3) ────────────────────────────────────────


@app.route("/wp-content/uploads/<path:filename>")
def wp_uploads(filename):
    # VULNERABLE: no normalization
    if ".." in filename:
        target = Path("/tmp") / filename.replace("../", "")
        if target.exists():
            return jsonify({"file": target.name, "content": target.read_text(errors="ignore")[:2000]})
    return jsonify({"error": "not found"}), 404


# ── Plugin version disclosure (flaw 6) ─────────────────────────────


@app.route("/wp-content/plugins/<plugin>/readme.txt")
def plugin_readme(plugin):
    if plugin == "wp-file-manager":
        return "=== WP File Manager ===\nContributors: wpfilemanager\nTags: file manager\nRequires at least: 5.0\nTested up to: 6.4\nStable tag: 6.9\n\nVulnerable to unauthenticated file upload in versions < 7.1"
    if plugin == "wp-sessions":
        return "=== WP Sessions ===\nStable tag: 1.4\n\nVulnerable to SQL injection in search parameter"
    return "Plugin not found", 404


# ── Unauthenticated file upload (flaw 7) ──────────────────────────


@app.route("/wp-admin/admin-ajax.php", methods=["POST"])
def wp_ajax():
    data = request.json if request.is_json else request.form
    action = data.get("action", "")
    if action == "upload":
        # VULNERABLE: no auth check, no extension check
        f = request.files.get("file")
        if f:
            upload_dir = Path("/tmp/wp_uploads")
            upload_dir.mkdir(exist_ok=True)
            f.save(upload_dir / f.filename)
            return jsonify({"uploaded": True, "file": f.filename, "path": str(upload_dir / f.filename)})
        return jsonify({"error": "no file"})
    return jsonify({"actions": ["upload", "search", "session_list"]})


# ── XXE (flaw 4) ───────────────────────────────────────────────────


@app.route("/xmlrpc.php", methods=["POST"])
def xmlrpc():
    xml_data = request.get_data(as_text=True)
    # VULNERABLE: XML parsing without XXE protection
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_data)
        method = root.find(".//methodName")
        if method is not None and method.text == "pingback.ping":
            source = root.find(".//string[1]")
            if source is not None and "file://" in source.text:
                # XXE: file:// URI is "fetched"
                path = source.text.replace("file://", "")
                if Path(path).exists():
                    return jsonify({"success": True, "content": Path(path).read_text(errors="ignore")[:500]})
            return jsonify({"success": True, "method": "pingback.ping"})
        return jsonify({"methods": ["system.listMethods", "wp.getUsersBlogs", "pingback.ping"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Info disclosure via options ────────────────────────────────────


@app.route("/wp-json/wp/v2/settings")
def wp_settings():
    conn = _db()
    opts = conn.execute("SELECT * FROM options").fetchall()
    conn.close()
    return jsonify({r["option_name"]: r["option_value"] for r in opts})


if __name__ == "__main__":
    import os

    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5904")), debug=False)
