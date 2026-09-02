"""
DevOps Dashboard — Internal Monitoring Tool
=============================================
Hard lab with RCE endpoint. Multi-step exploit chain required.
Port: 5700
Flag: /tmp/suijin_flag.txt (requires RCE to read)
"""
import hashlib
import json
import os
import sqlite3
import subprocess

from flask import Flask, g, redirect, render_template_string, request, session

app = Flask(__name__)
app.secret_key = 'd3v0ps_s3cr3t_k3y_5700'
DB = os.path.join(os.path.dirname(__file__), 'devops.db')
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Seed a log file
if not os.path.exists(os.path.join(LOG_DIR, 'access.log')):
    with open(os.path.join(LOG_DIR, 'access.log'), 'w') as f:
        f.write('127.0.0.1 - [26/Jul/2026:00:00:01] "GET / HTTP/1.1" 200\n')
        f.write('127.0.0.1 - [26/Jul/2026:00:00:05] "POST /login HTTP/1.1" 302\n')
        f.write('127.0.0.1 - [26/Jul/2026:00:01:00] "GET /dashboard HTTP/1.1" 200\n')
        f.write('SECRET: admin backup password = "DevOpsAdmin#2026!" stored in env\n')

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db

def init_db():
    db = sqlite3.connect(DB)
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT,
        role TEXT DEFAULT 'user', settings TEXT DEFAULT '{}')''')
    db.execute('''CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY, user_id INTEGER, key TEXT, permissions TEXT)''')
    def h(p): return hashlib.sha256(p.encode()).hexdigest()
    users = [
        ('admin', h('DevOpsAdmin#2026!'), 'admin'),
        ('john.dev', h('password123'), 'developer'),
        ('sarah.ops', h('ops2026!'), 'operator'),
        ('svc_monitor', h('m0n1t0r_s3cr3t'), 'service'),
    ]
    for u, p, r in users:
        try: db.execute('INSERT INTO users (username, password_hash, role) VALUES (?,?,?)', (u, p, r))
        except Exception:

            pass  # Lab test apppass
    # API keys
    keys = [
        (1, 'dk_api_4a2f8b1c9d3e', 'full_access'),
        (4, 'dk_mon_7x3y1z9w', 'read_only'),
    ]
    for uid, k, perm in keys:
        try: db.execute('INSERT INTO api_keys (user_id, key, permissions) VALUES (?,?,?)', (uid, k, perm))
        except Exception:

            pass  # Lab test apppass
    db.commit(); db.close()

@app.teardown_appcontext
def close_db(e):
    db = g.pop('db', None)
    if db: db.close()

# ── Routes ────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return RENDER('index.html', error='')

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = ''
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        db = get_db()
        # VULN: SQL injection
        q = f"SELECT * FROM users WHERE username='{username}'"
        try: user = db.execute(q).fetchone()
        except Exception:

            pass  # Lab test appuser = None
        if user:
            # VULN: SQLi bypass — password check always passes when row exists
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['username'] = user['username']
            return redirect('/dashboard')
        error = 'Invalid credentials'
    return RENDER('login.html', error=error)

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect('/login')
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    settings = json.loads(user['settings'] or '{}')
    msg = settings.get('custom_message', '')
    # VULN: SSTI — custom_message rendered via render_template_string
    if msg and ('{{' in msg or '{%' in msg):
        try: msg = render_template_string(msg)
        except Exception:

            pass  # Lab test apppass
    return RENDER('dashboard.html', user=user, message=msg)

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session: return redirect('/login')
    db = get_db()
    update_msg = ''
    if request.method == 'POST':
        new_settings = {
            'custom_message': request.form.get('custom_message', ''),
            'theme': request.form.get('theme', 'dark'),
            'refresh_interval': request.form.get('refresh_interval', '30'),
        }
        db.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(new_settings), session['user_id']))
        db.commit()
        update_msg = 'Settings saved'
    user = db.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    current = json.loads(user['settings'] or '{}')
    return RENDER('settings.html', user=user, settings=current, update_msg=update_msg)

@app.route('/admin')
def admin():
    if 'user_id' not in session or session.get('role') != 'admin': return 'Access denied', 403
    db = get_db()
    users = db.execute('SELECT id, username, role FROM users').fetchall()
    keys = db.execute('SELECT * FROM api_keys').fetchall()
    return RENDER('admin.html', users=users, keys=keys)

@app.route('/admin/ping', methods=['GET', 'POST'])
def admin_ping():
    if 'user_id' not in session or session.get('role') != 'admin': return 'Access denied', 403
    output = ''
    if request.method == 'POST':
        host = request.form.get('host', '127.0.0.1')
        # VULN: Command injection — shell=True with unsanitized input
        cmd = f"ping -c 2 {host}"
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=5).decode()
        except Exception as e:
            output = str(e)
    return RENDER('ping.html', output=output)

@app.route('/admin/files', methods=['GET', 'POST'])
def admin_files():
    if 'user_id' not in session or session.get('role') != 'admin': return 'Access denied', 403
    msg = ''
    if request.method == 'POST':
        file = request.files.get('file')
        if file and file.filename:
            fname = file.filename
            # VULN: Weak extension filter — only blocks .py, not .pyc, .pth, .sh
            blocked = ['.py']
            if any(fname.lower().endswith(ext) for ext in blocked):
                msg = f'Blocked extension: {fname}'
            else:
                filepath = os.path.join(UPLOAD_DIR, fname)
                file.save(filepath)
                msg = f'Uploaded: {fname}'
    files = os.listdir(UPLOAD_DIR)
    return RENDER('files.html', files=files, msg=msg)

@app.route('/admin/exec', methods=['GET', 'POST'])
def admin_exec():
    if 'user_id' not in session or session.get('role') != 'admin': return 'Access denied', 403
    output = ''
    if request.method == 'POST':
        script = request.form.get('script', '')
        script_path = os.path.join(UPLOAD_DIR, script)
        if os.path.exists(script_path) and script_path.endswith(('.sh', '.pyc', '.txt')):
            try:
                output = subprocess.check_output(['python3', script_path], stderr=subprocess.STDOUT, timeout=5).decode()
            except Exception as e:
                output = str(e)
        else:
            output = 'Script not found or invalid type'
    return RENDER('exec.html', output=output, upload_dir=UPLOAD_DIR)

@app.route('/api/users')
def api_users():
    key = request.headers.get('X-API-Key', '')
    db = get_db()
    k = db.execute('SELECT * FROM api_keys WHERE key=?', (key,)).fetchone()
    if not k: return json.dumps({'error':'Invalid API key'}), 401
    users = db.execute('SELECT id, username, role FROM users').fetchall()
    return json.dumps([dict(u) for u in users])

@app.route('/api/users/<int:uid>')
def api_user(uid):
    # VULN: IDOR — no auth check beyond API key, and key can be any valid key
    key = request.headers.get('X-API-Key', '')
    db = get_db()
    k = db.execute('SELECT * FROM api_keys WHERE key=?', (key,)).fetchone()
    if not k: return json.dumps({'error':'Invalid API key'}), 401
    user = db.execute('SELECT id, username, role FROM users WHERE id=?', (uid,)).fetchone()
    if not user: return json.dumps({'error':'Not found'}), 404
    return json.dumps(dict(user))

@app.route('/logs')
def view_logs():
    if 'user_id' not in session: return redirect('/login')
    log_file = request.args.get('file', 'access.log')
    # VULN: Path traversal
    log_path = os.path.join(LOG_DIR, log_file)
    try:
        content = open(log_path).read()
    except Exception:
        content = 'Log file not found'
    return RENDER('logs.html', content=content, log_file=log_file)

@app.route('/static/js/app.js')
def serve_js():
    # VULN: Hardcoded API key in JS
    js = f'''
    const API_BASE = '/api';
    const DEFAULT_API_KEY = 'dk_api_4a2f8b1c9d3e';
    async function fetchUsers() {{
        const r = await fetch('{API_BASE}/users', {{
            headers: {{'X-API-Key': DEFAULT_API_KEY}}
        }});
        return r.json();
    }}
    '''
    from flask import Response
    return Response(js, mimetype='application/javascript')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ── Template helpers ──────────────────────────────────────────────────
TEMPLATES = {}
def _load_templates():
    global TEMPLATES
    TEMPLATES['index.html'] = '''
<!DOCTYPE html><html><head><title>DevOps Dashboard</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{font:14px monospace;background:#0d1117;color:#c9d1d9;padding:40px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:24px;margin:16px 0;max-width:600px}
h1,h2{color:#f0f6fc}a{color:#58a6ff}input,button{padding:8px 12px;margin:6px 0;width:100%;border:1px solid #30363d;border-radius:6px;background:#0d1117;color:#c9d1d9}
button{background:#238636;cursor:pointer}button:hover{background:#2ea043}button.danger{background:#da3633}
.error{color:#f85149;background:#490202;padding:8px;border-radius:4px;margin:8px 0}
.success{color:#3fb950;background:#04260f;padding:8px;border-radius:4px;margin:8px 0}
pre{background:#0d1117;border:1px solid #30363d;padding:12px;border-radius:6px;overflow-x:auto}
nav{padding:12px 0;border-bottom:1px solid #30363d;margin-bottom:24px}
nav a{margin-right:16px}</style></head><body>
<nav><a href="/">Home</a>{% if session.user_id %}<a href="/dashboard">Dashboard</a><a href="/settings">Settings</a>{% if session.role=="admin" %}<a href="/admin">Admin</a>{% endif %}<a href="/logs">Logs</a><a href="/logout">Logout</a>{% else %}<a href="/login">Login</a>{% endif %}</nav>
<div class="card"><h1>DevOps Dashboard</h1><p>Internal monitoring tool v3.2.1</p></div>
{% if error %}<div class="error">{{error}}</div>{% endif %}
{% block content %}{% endblock %}
</body></html>'''

    TEMPLATES['login.html'] = '''{% extends "index.html" %}{% block content %}
<div class="card"><h2>Sign In</h2>{% if error %}<div class="error">{{error}}</div>{% endif %}
<form method="POST"><input name="username" placeholder="Username" required><input name="password" type="password" placeholder="Password" required><button type="submit">Sign In</button></form></div>{% endblock %}'''

    TEMPLATES['dashboard.html'] = '''{% extends "index.html" %}{% block content %}
<div class="card"><h2>Welcome, {{user.username}}</h2><p>Role: {{user.role}}</p></div>
{% if message %}<div class="card">{{message|safe}}</div>{% endif %}
{% endblock %}'''

    TEMPLATES['settings.html'] = '''{% extends "index.html" %}{% block content %}
<div class="card"><h2>Settings</h2>{% if update_msg %}<div class="success">{{update_msg}}</div>{% endif %}
<form method="POST"><label>Custom Dashboard Message (supports HTML)</label><textarea name="custom_message" rows="4">{{settings.custom_message or ''}}</textarea>
<label>Theme</label><select name="theme"><option value="dark" {% if settings.theme=="dark" %}selected{% endif %}>Dark</option><option value="light">Light</option></select>
<label>Refresh Interval (seconds)</label><input name="refresh_interval" value="{{settings.refresh_interval or '30'}}">
<button type="submit">Save</button></form></div>{% endblock %}'''

    TEMPLATES['admin.html'] = '''{% extends "index.html" %}{% block content %}
<div class="card"><h2>Admin Panel</h2><a href="/admin/ping"><button>Ping Test</button></a><a href="/admin/files"><button>File Manager</button></a><a href="/admin/exec"><button>Script Exec</button></a></div>
<div class="card"><h2>Users</h2><table style="width:100%"><tr><th>ID</th><th>Username</th><th>Role</th></tr>{% for u in users %}<tr><td>{{u.id}}</td><td>{{u.username}}</td><td>{{u.role}}</td></tr>{% endfor %}</table></div>{% endblock %}'''

    TEMPLATES['ping.html'] = '''{% extends "index.html" %}{% block content %}
<div class="card"><h2>Ping Test</h2><form method="POST"><input name="host" placeholder="Host to ping" value="127.0.0.1"><button type="submit">Ping</button></form>
{% if output %}<pre>{{output}}</pre>{% endif %}</div>{% endblock %}'''

    TEMPLATES['files.html'] = '''{% extends "index.html" %}{% block content %}
<div class="card"><h2>File Manager</h2>{% if msg %}<div class="success">{{msg}}</div>{% endif %}
<form method="POST" enctype="multipart/form-data"><input type="file" name="file"><button type="submit">Upload</button></form>
<h2>Files</h2><ul>{% for f in files %}<li>{{f}}</li>{% endfor %}</ul></div>{% endblock %}'''

    TEMPLATES['exec.html'] = '''{% extends "index.html" %}{% block content %}
<div class="card"><h2>Script Exec (uploaded files only)</h2>
<form method="POST"><input name="script" placeholder="script.sh"><button class="danger" type="submit">Execute</button></form>
{% if output %}<pre>{{output}}</pre>{% endif %}</div>{% endblock %}'''

    TEMPLATES['logs.html'] = '''{% extends "index.html" %}{% block content %}
<div class="card"><h2>Log Viewer — {{log_file}}</h2><pre>{{content}}</pre></div>{% endblock %}'''

_load_templates()

def RENDER(name, **kwargs):
    from jinja2 import BaseLoader, Environment
    env = Environment(loader=BaseLoader())
    tmpl = TEMPLATES.get(name, '')
    if '{% extends' in tmpl:
        # Simple extends handling
        base = TEMPLATES.get('index.html', '')
        block_match = __import__('re').search(r'{% block content %}(.*?){% endblock %}', tmpl, __import__('re').DOTALL)
        if block_match:
            content = block_match.group(1)
            tmpl = base.replace('{% block content %}{% endblock %}', content)
    return render_template_string(tmpl, **kwargs, session=session)

if __name__ == '__main__':
    import sys
    if not os.path.exists(DB):
        init_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5700
    app.run(host='127.0.0.1', port=port, debug=False)
