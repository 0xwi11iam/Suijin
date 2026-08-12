import os, json, re, subprocess, threading, time
import xmlrpc.client, requests, urllib3, shlex, sqlite3
from pathlib import Path

# Module system — load modular tool packs from modules/
from medusa.modules.loader import discover_modules, get_module_tools

# Extracted modules — guardrails and workspace path management
from medusa.tools.guardrails import is_dangerous, confirm_global_action, _BLOCKED_PATTERNS
from medusa.tools.workspace import resolve_workspace_path, WORKSPACE_DIR

MCP_SERVERS = {}
def get_server_for_tool(tool_name: str) -> list:
    return TOOL_MCP_MATRIX.get(tool_name, [])
AI_SERVICE_ENDPOINTS = {}
def fingerprint_ai_response(response_json: dict) -> str:
    return "unknown"

_recon_state = {"exploration_count": 0}


def reset_recon_state():
    """Reset the exploration counter (call at start of new engagement)."""
    _recon_state["exploration_count"] = 0

discover_modules()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent

_jobs: dict[str, dict] = {}
_job_lock = threading.Lock()
DB_PATH = BASE_DIR / "kb.sqlite3"
global_session = requests.Session()
_proxy_url = None


def set_proxy(url: str | None):
    """Set a global proxy for all HTTP requests. Call at startup from config."""
    global _proxy_url
    _proxy_url = url
    if url:
        global_session.proxies = {"http": url, "https": url}
    else:
        global_session.proxies = {}


def get_proxy() -> str | None:
    return _proxy_url

# Ensure workspace subdirectories exist
(WORKSPACE_DIR / "payloads").mkdir(parents=True, exist_ok=True)
(WORKSPACE_DIR / "scripts").mkdir(parents=True, exist_ok=True)
(WORKSPACE_DIR / "outputs").mkdir(parents=True, exist_ok=True)


def truncate(text, limit=50000):
    if len(text) > limit:
        return text[:limit] + f"\n\n[TRUNCATED at {limit} chars — {len(text)} total]"
    return text

def search_kb(keyword):
    """Search the local knowledge base. Gracefully degrades if KB not built."""
    if not DB_PATH.exists():
        return "Knowledge base not built yet. Use check_knowledge or record_finding to query the in-memory knowledge graph instead."
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        query = f"%{keyword}%"
        c.execute("SELECT path, content FROM kb_files WHERE content LIKE ? OR path LIKE ? LIMIT 3", (query, query))
        rows = c.fetchall()
        conn.close()
        if not rows:
            return f"No matching entries found for '{keyword}'."
        res = ""
        for path, content in rows:
            res += f"--- Source: {path} ---\n{content[:2000]}\n\n"
        return truncate(res)
    except Exception as e:
        return f"KB Error: {str(e)}"

def execute_terminal(cmd, timeout=30):
    """Shell execution gateway — scoped to medusa_agent/ workspace.

    Commands that modify the global system (pip install, brew, apt, sudo, etc.)
    are intercepted and require explicit user approval before execution.
    """
    try:
        if not cmd:
            return "Error: No command provided."

        # Self-Kill Protection
        my_pid = str(os.getpid())
        cmd_tokens = cmd.replace(";", " ").replace("&&", " ").replace("|", " ").split()
        if "kill" in cmd_tokens and my_pid in cmd_tokens:
            return f"SYSTEM OVERRIDE: Refusing to execute command. {my_pid} is the AI Agent's own Process ID. You must find the target application's PID."

        # Global-action gate: intercept dangerous commands
        is_dangerous, pattern = is_dangerous(cmd)
        if is_dangerous:
            if not confirm_global_action(cmd, pattern):
                return f"⛔ Command denied by user (matched: {pattern}).\nCommand was: {cmd[:200]}"
            # Approved — proceed with execution

        # Build environment with homebrew paths (macOS)
        env = os.environ.copy()
        brew_paths = ['/opt/homebrew/bin', '/usr/local/bin', '/opt/homebrew/sbin']
        current_path = env.get('PATH', '')
        for bp in brew_paths:
            if bp not in current_path:
                current_path = f"{bp}:{current_path}"
        env['PATH'] = current_path

        # Run with shell=False using tokenized command list
        import shlex
        try:
            cmd_parts = shlex.split(cmd)
        except ValueError:
            cmd_parts = ["/bin/sh", "-c", cmd]

        process = subprocess.run(
            cmd_parts if len(cmd_parts) > 1 else ["/bin/sh", "-c", cmd],
            capture_output=True, text=True,
            timeout=timeout, cwd=str(WORKSPACE_DIR), env=env,
        )
        out = ""
        if process.stdout:
            out += f"[STDOUT]\n{process.stdout}\n"
        if process.stderr:
            out += f"[STDERR]\n{process.stderr}\n"
        return truncate(out if out else "Executed (No Output).")
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds (no output was received)."
    except Exception as e:
        return f"Execution Fault: {str(e)}"

def http_request(method, url, headers=None, body=""):
    """Advanced Browser Mimicry Engine with session awareness and rate limiting."""
    try:
        # Rate limit check
        from medusa.tools.session_aware import is_rate_limited, record_response, get_session, jitter
        if is_rate_limited(url):
            return f"RATE LIMITED: Target {url} is throttling requests. Wait and retry with jitter."
        
        req_headers = headers if headers else {}
        default_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Upgrade-Insecure-Requests": "1"
        }
        for k, v in default_headers.items():
            if k not in req_headers:
                req_headers[k] = v

        # Track session cookies
        session = get_session(url)
        if session.get_cookie_string():
            req_headers["Cookie"] = session.get_cookie_string()

        resp = global_session.request(
            method=method.upper(), url=url, headers=req_headers, data=body, timeout=20, verify=False, allow_redirects=True
        )
        record_response(url, resp.status_code, dict(resp.headers))
        session.update_from_response(dict(resp.headers), resp.text)
        session.touch()

        out =[f"Status: {resp.status_code}", f"Headers: {dict(resp.headers)}", f"Cookies: {dict(global_session.cookies)}", f"Body:\n{resp.text}"]
        return truncate("\n".join(out))
    except Exception as e: return f"HTTP Error: {str(e)}"

def apply_patch(vulnerability, file_path="lab.py"):
    """Apply a pre-defined security patch to the target file."""
    full_path = BASE_DIR / file_path
    if not full_path.exists():
        return f"Error: {file_path} not found."
    
    code = full_path.read_text(encoding='utf-8', errors='ignore')
    original = code
    patched = False
    import re as _re
    
    vuln = vulnerability.lower()
    
    if vuln in ["sqli", "sql injection", "sql_injection"]:
        # Try exact match first, then regex fallback
        old_login = 'query = f"SELECT * FROM users WHERE username=\'{username}\' AND password=\'{password}\'"'
        new_login = 'query = "SELECT * FROM users WHERE username=? AND password=?"'
        if old_login in code:
            code = code.replace(old_login, new_login)
            code = code.replace('cur = db.execute(query)', 'cur = db.execute(query, (username, password))')
            patched = True
        else:
            # Regex fallback: find f-string SQL patterns and parameterize them
            sqli_pattern = _re.compile(r'(?:query|sql|q)\s*=\s*f["\'].*?SELECT.*?\{.*?\}.*?["\']', _re.IGNORECASE | _re.DOTALL)
            matches = sqli_pattern.findall(code)
            for match in matches:
                comment = f"# [MEDUSA PATCH] Original vulnerable query: {match[:80]}...\n# Replace with parameterized query: cursor.execute(sql, (param1, param2))"
                code = code.replace(match, comment + "\n" + match, 1)
                patched = True
    
    elif vuln in ["command injection", "command_injection", "cmdi"]:
        # Fix command injection in /ping_exec
        old_cmd = 'cmd = f"ping -c 2 {host}"'
        new_cmd = 'cmd = ["ping", "-c", "2", host]'
        if old_cmd in code:
            code = code.replace(old_cmd, new_cmd)
            code = code.replace(
                'output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=5)',
                'output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=5)'
            )
            patched = True
    
    elif vuln in ["ssrf", "lfi", "file inclusion"]:
        # Fix SSRF/LFI in /webhook_fetch
        old_lfi = '''if url.startswith("file://"):
        path = url[7:]
        with open(path, 'r') as f:
            return f"<pre>{f.read()}</pre>"'''
        new_lfi = '''if url.startswith("file://") or url.startswith("file:"):
        return "Error: File protocol not allowed for security reasons."
    if re.match(r'https?://(127\\.|10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[0-1])|0\\.0\\.0\\.0|localhost)', url):
        return "Error: Internal/private addresses are not allowed."'''
        if old_lfi in code:
            code = code.replace(old_lfi, new_lfi)
            patched = True
    
    elif vuln in ["ssti", "template injection"]:
        # Fix SSTI in /set_announcement
        old_ssti = 'template = "<h2>Announcement:</h2>" + msg\n    return render_template_string(template)'
        new_ssti = 'safe_msg = msg.replace(\'{{\', \'\').replace(\'}}\', \'\').replace(\'{%\', \'\').replace(\'%}\', \'\')\n    return f"<h2>Announcement:</h2>{safe_msg}"'
        if old_ssti in code:
            code = code.replace(old_ssti, new_ssti)
            patched = True
    
    elif vuln in ["idor", "xss", "stored xss", "idor_xss"]:
        # Fix IDOR in /profile
        old_profile = 'def profile(user_id):\n    cur = db.execute(f"SELECT * FROM users WHERE id={user_id}")'
        new_profile = 'def profile(user_id):\n    if \'user_id\' not in session or session[\'user_id\'] != user_id:\n        return "Access denied", 403\n    cur = db.execute("SELECT * FROM users WHERE id=?", (user_id,))'
        if old_profile in code:
            code = code.replace(old_profile, new_profile)
            patched = True
        
        # Fix Stored XSS in /comment
        old_xss = "db.execute(f\"INSERT INTO comments (user_id, text) VALUES ({user_id}, '{text}')\")"
        new_xss = 'import html as html_module\n    safe_text = html_module.escape(text)\n    db.execute("INSERT INTO comments (user_id, text) VALUES (?, ?)", (user_id, safe_text))'
        if old_xss in code:
            code = code.replace(old_xss, new_xss)
            patched = True
    
    else:
        return f"Unknown vulnerability type: {vulnerability}. Supported: sqli, cmdi, ssrf, ssti, xss, idor_xss"
    
    if patched:
        full_path.write_text(code, encoding='utf-8')
        return f"Patched {file_path} for {vulnerability}"
    else:
        return f"Could not find exact vulnerable pattern for {vulnerability} in {file_path}. The file may already be patched or use different code. Consider manual review."


def read_file(file_path):
    """Read a file — scoped to the agent workspace by default.

    - Relative paths → resolved from medusa_agent/
    - Absolute paths → allowed (read-only, no system impact)
    """
    target = resolve_workspace_path(file_path)
    if not target.exists():
        return f"Error: File not found: {target}"
    try:
        return truncate(target.read_text(encoding='utf-8', errors='ignore'))
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(file_path, content):
    """Write content to a file — scoped to the agent workspace by default.

    - Relative paths → resolved from medusa_agent/
    - Absolute paths → allowed but the write location is noted in output
    """
    target = resolve_workspace_path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(str(content), encoding="utf-8")
        rel = target.relative_to(WORKSPACE_DIR) if str(target).startswith(str(WORKSPACE_DIR)) else None
        loc = f"medusa_agent/{rel}" if rel else str(target)
        return f"File written: {loc}"
    except Exception as e:
        return f"Error writing file: {e}"


# ----------------------------------------------------------------------
# Metasploit integration
# ----------------------------------------------------------------------

def _msf_rpc_connect(config):
    """Connect to an msfrpcd daemon and return (proxy, token) or (None, error)."""
    host = config.get("metasploit_rpc_host", "127.0.0.1")
    port = int(config.get("metasploit_rpc_port", 55553))
    password = os.environ.get("MSF_RPC_PASSWORD", "")
    if not password:
        return None, "Metasploit RPC password not set in config (metasploit_rpc_password)."
    try:
        proxy = xmlrpc.client.ServerProxy(
            f"http://{host}:{port}/api/",
            allow_none=True,
            use_datetime=True,
        )
        auth = proxy.auth.login("msf", password)
        if auth.get("result") == "success":
            return proxy, auth.get("token")
        else:
            return None, f"Metasploit RPC auth failed: {auth.get('error', 'unknown')}"
    except Exception as e:
        return None, f"Metasploit RPC connection error: {e}"


def _msf_console_fallback(cmd, config=None):
    """Fallback: run msfconsole -q -x with a command and return output."""
    import tempfile, time
    out_path = BASE_DIR / f".msf_out_{int(time.time())}.txt"
    full_cmd = f"msfconsole -q -x {shlex.quote(cmd)} -o {shlex.quote(str(out_path))}"
    try:
        subprocess.run(full_cmd, shell=True, timeout=60,
                       capture_output=True, text=True)
        if out_path.exists():
            text = out_path.read_text(encoding="utf-8", errors="replace")
            out_path.unlink(missing_ok=True)
            return truncate(text.strip() or "(no output)")
        return "(no output written)"
    except subprocess.TimeoutExpired:
        out_path.unlink(missing_ok=True)
        return "Error: msfconsole timed out after 60 seconds."
    except FileNotFoundError:
        return "Error: msfconsole not found. Install Metasploit or check PATH."
    except Exception as e:
        out_path.unlink(missing_ok=True)
        return f"Error: msfconsole execution failed: {e}"


def msf_check(config):
    """Probe whether Metasploit is available (RPC first, then console)."""
    proxy, token = _msf_rpc_connect(config)
    if proxy is not None:
        try:
            version = proxy.core.version(token)
            return (
                f"Metasploit RPC connected.\n"
                f"Version: {version}\n"
                f"RPC: {config.get('metasploit_rpc_host', '127.0.0.1')}:"
                f"{config.get('metasploit_rpc_port', 55553)}"
            )
        except Exception as e:
            return f"Metasploit RPC connected but core.version failed: {e}"

    # Fallback: check if msfconsole exists
    try:
        r = subprocess.run("which msfconsole", shell=True, capture_output=True,
                           text=True, timeout=5)
        if r.stdout.strip():
            return (
                f"Metasploit available via msfconsole at: {r.stdout.strip()}\n"
                "No RPC daemon detected. Use msf_command with console fallback.\n"
                "To enable RPC, set metasploit_rpc_host/port/password in config."
            )
    except Exception:
        pass
    return (
        "Metasploit NOT detected.\n"
        "Install from: https://www.metasploit.com/\n"
        "Or start msfrpcd for RPC access."
    )


def msf_command(cmd, config):
    """Run a raw Metasploit command via RPC or msfconsole fallback."""
    proxy, token = _msf_rpc_connect(config)
    if proxy is not None:
        try:
            # Create a temporary console for this command
            console_info = proxy.console.create(token)
            cid = console_info.get("id")
            if not cid:
                return f"Error: failed to create console — {console_info}"
            # Write the command
            proxy.console.write(token, cid, cmd + "\n")
            # Wait a beat then read
            import time
            time.sleep(1.5)
            output = proxy.console.read(token, cid)
            data = output.get("data", "")
            # Destroy the console
            proxy.console.destroy(token, cid)
            return truncate(data.strip() or "(no output)")
        except Exception as e:
            return f"Error: RPC command failed — {e}"

    # Fallback to msfconsole
    return _msf_console_fallback(cmd, config)


def msf_run(module, payload, options, config):
    """Configure and execute a Metasploit module via RPC.

    Args:
        module:  e.g. "exploit/multi/handler"
        payload: e.g. "windows/meterpreter/reverse_tcp" (optional for aux)
        options: dict of module options, e.g. {"RHOSTS": "10.0.0.1"}
        config:  app config for RPC connection info
    """
    proxy, token = _msf_rpc_connect(config)
    if proxy is None:
        # Build a resource script as fallback
        lines = [f"use {module}"]
        if isinstance(options, dict):
            for k, v in options.items():
                lines.append(f"set {k} {v}")
        if payload:
            lines.append(f"set PAYLOAD {payload}")
        lines.append("run -j")
        return _msf_console_fallback("; ".join(lines))

    try:
        # Set payload if provided
        if payload:
            proxy.module.execute(token, "auxiliary" if "/aux" in module or module.startswith("aux")
                                 else "exploit",
                                 module, {"PAYLOAD": payload})

        # Build options dict
        opts = dict(options) if isinstance(options, dict) else {}

        # Determine module type from path
        mtype = "auxiliary"
        if module.startswith("exploit") or "/exploit" in module:
            mtype = "exploit"
        elif module.startswith("post") or "/post" in module:
            mtype = "post"
        elif module.startswith("payload") or "/payload" in module:
            mtype = "payload"
        elif module.startswith("nop") or "/nop" in module:
            mtype = "nop"
        elif module.startswith("encoder") or "/encoder" in module:
            mtype = "encoder"

        # Execute
        result = proxy.module.execute(token, mtype, module, opts)
        # Return structured result
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error: msf_run failed — {e}"


def msf_sessions(action, session_id, config):
    """Manage active Metasploit sessions.

    action: "list" | "interact" | "kill"
    session_id: required for interact/kill
    """
    proxy, token = _msf_rpc_connect(config)
    if proxy is None:
        if action == "list":
            return _msf_console_fallback("sessions -l")
        elif action == "kill":
            return _msf_console_fallback(f"sessions -k {session_id}")
        else:
            return "Error: RPC required for session interaction. Start msfrpcd."

    try:
        if action == "list":
            sessions = proxy.session.list(token)
            return json.dumps(sessions, indent=2, default=str)
        elif action == "kill" and session_id:
            result = proxy.session.stop(token, session_id)
            return json.dumps(result, indent=2, default=str)
        elif action == "interact" and session_id:
            # Read recent output from a session
            result = proxy.session.read(token, session_id)
            return json.dumps(result, indent=2, default=str)
        else:
            return f"Error: msf_sessions needs action=list|interact|kill (got '{action}')"
    except Exception as e:
        return f"Error: msf_sessions failed — {e}"


# ----------------------------------------------------------------------
# NVD CVE search (NIST National Vulnerability Database)
# ----------------------------------------------------------------------
NVD_BASE = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def search_cve(software, config, version=None, limit=5):
    """Search NIST NVD for CVEs matching a software product/version.

    Args:
        software: product name (e.g. "apache httpd", "openssh")
        config:   app config (may contain nvd_api_key for higher rate limits)
        version:  optional version string (e.g. "2.4.49")
        limit:    max results to return (1-20, default 5)
    """
    if not software or not software.strip():
        return "Error: search_cve requires a 'software' argument."

    limit = max(1, min(int(limit or 5), 20))
    software = software.strip()
    version_str = version.strip() if version else None

    api_key = os.environ.get("NVD_API_KEY", "").strip()
    headers = {"User-Agent": "Medusa/1.0"}

    def _do_nvd(params):
        try:
            resp = requests.get(NVD_BASE, params=params, headers=headers, timeout=30)
            if resp.status_code != 200:
                return {"vulnerabilities": [], "totalResults": 0,
                        "_error": f"NVD API returned HTTP {resp.status_code}"}
            return resp.json()
        except requests.exceptions.Timeout:
            return {"vulnerabilities": [], "totalResults": 0,
                    "_error": "NVD API request timed out (30s)."}
        except requests.exceptions.ConnectionError:
            return {"vulnerabilities": [], "totalResults": 0,
                    "_error": "Cannot reach NVD (services.nvd.nist.gov). Check network."}
        except Exception as e:
            return {"vulnerabilities": [], "totalResults": 0,
                    "_error": f"NVD API request failed — {e}"}

    # Strategy: prefer cpeName for precise vendor/product matching,
    # fall back to keyword search with version filtering.
    data = None
    error = None
    max_total = 0
    fetch_limit = min(limit * 5, 50)  # fetch more than needed for local sorting

    # Strategy 1: exact version match in description (usually works)
    for strategy in range(3):
        params = {"resultsPerPage": fetch_limit}
        if api_key:
            params["apiKey"] = api_key

        if strategy == 0 and version_str:
            # Keyword search: "product version" (e.g. "Apache HTTP Server 2.4.49")
            # NVD descriptions write it as "Apache HTTP Server" not "apache httpd"
            # so use JUST the core product keyword + version for best hit rate
            keywords = software.split()
            core = keywords[0] if len(keywords) == 1 else software
            params["keywordSearch"] = f"{core} {version_str}"
        elif strategy == 1:
            # Keyword search: just the product name
            params["keywordSearch"] = software
        else:
            # Broader keyword search for multi-word products
            params["keywordSearch"] = " ".join(software.split()[:2])

        data = _do_nvd(params)
        if data.get("_error"):
            error = data["_error"]
            continue
        if data.get("totalResults", 0) > 0:
            error = None
            break
        max_total = max(max_total, data.get("totalResults", 0))

    if error and max_total == 0:
        return error

    vulns = data.get("vulnerabilities", []) if data else []
    if not vulns:
        q = f"{software} {version_str}" if version_str else software
        return f"No CVEs found for: {q}"

    # Build list of (score_float, cve_item) tuples for sorting by severity.
    # Fetch more than we need so we can sort and pick the most relevant.
    scored = []
    for item in vulns:
        cve = item.get("cve", {})
        score_val, _ = _extract_cvss(cve)
        try:
            score_float = float(score_val) if score_val != "N/A" else 0.0
        except (ValueError, TypeError):
            score_float = 0.0

        # Boost CVEs that mention the exact version in their description
        if version_str:
            descs = str(cve.get("descriptions", ""))
            if version_str in descs:
                score_float += 100  # huge boost — these are exact matches

        scored.append((score_float, item))

    # Sort: highest score first, then filter to limit
    scored.sort(key=lambda x: -x[0])
    vulns = [item for _, item in scored[:limit]]

    # If we have a version and did a broad search, filter results to
    # those whose description mentions the version string
    if version_str and strategy > 0:
        filtered = [v for v in vulns if version_str in str(v.get("cve", {}).get("descriptions", ""))]
        if filtered:
            vulns = filtered
        # If nothing matches after filtering, still show what we have

    results = []
    for item in vulns:
        cve = item.get("cve", {})
        cve_id = cve.get("id", "UNKNOWN")

        # Description
        desc_text = ""
        for d in cve.get("descriptions", []):
            if d.get("lang") == "en":
                desc_text = d.get("value", "")
                break

        # CVSS score — prefer v3.1, fallback to v3.0, v2.0
        score, severity = _extract_cvss(cve)

        # CISA KEV flag
        kev = "🔴 ACTIVELY EXPLOITED" if _is_kev(cve) else ""

        # Extract valuable reference URLs
        refs = []
        for r in cve.get("references", []):
            url = r.get("url", "")
            source = r.get("source", "")
            for tag in (r.get("tags") or []):
                tag_lower = tag.lower()
                if "exploit" in tag_lower or "patch" in tag_lower or "vendor" in tag_lower:
                    refs.append(f"  [{tag}] {url} ({source})")
                    break
        refs = refs[:3]

        # Weakness / CWE
        weaknesses = []
        for w in cve.get("weaknesses", []):
            for wd in w.get("description", []):
                val = wd.get("value", "")
                if val and val != "NVD-CWE-noinfo" and val != "NVD-CWE-Other":
                    weaknesses.append(val)
        cwe_str = ", ".join(weaknesses[:3])

        entry = (
            f"[{cve_id}] {severity} ({score})\n"
            f"  {desc_text[:300]}\n"
        )
        if cwe_str:
            entry += f"  CWE: {cwe_str}\n"
        if kev:
            entry += f"  {kev}\n"
        if refs:
            entry += "\n".join(refs) + "\n"

        results.append(entry)

    q = f"{software} {version_str}" if version_str else software
    total = len(results)
    header = f"Found {total} CVE(s) for '{q}':\n\n"
    return header + "\n".join(results)


def _extract_cvss(cve):
    """Extract the best CVSS score and severity label from a CVE object."""
    metrics = cve.get("metrics", {})

    # v3.1
    for entry in metrics.get("cvssMetricV31", []):
        cvss = entry.get("cvssData", {})
        score = cvss.get("baseScore")
        sev = cvss.get("baseSeverity", "")
        if score is not None:
            return f"{score:.1f}", sev

    # v3.0
    for entry in metrics.get("cvssMetricV30", []):
        cvss = entry.get("cvssData", {})
        score = cvss.get("baseScore")
        sev = cvss.get("baseSeverity", "")
        if score is not None:
            return f"{score:.1f}", sev

    # v2.0
    for entry in metrics.get("cvssMetricV2", []):
        cvss = entry.get("cvssData", {})
        score = cvss.get("baseScore")
        if score is not None:
            return f"{score:.1f}", "MEDIUM"
    return "N/A", "UNKNOWN"


def _is_kev(cve):
    """Check if the CVE is in CISA's Known Exploited Vulnerabilities catalog."""
    kevs = cve.get("cisaExploitAdd") or cve.get("cisaActionDue")
    if kevs:
        return True
    vuln_status = cve.get("vulnStatus", "")
    return "Known Exploited" in str(vuln_status)


# ----------------------------------------------------------------------
# Oracle / Knowledge Graph tools
# ----------------------------------------------------------------------

def check_knowledge(target, payload=None, config=None):
    """Query the knowledge graph for constraints on a target.

    If payload is provided, checks whether that specific payload matches
    any known blocked patterns. Otherwise returns full constraint summary.

    Args:
        target:  hostname or IP of the target
        payload: optional — specific payload to check against blocked patterns
        config:  ignored (kg is independent of config)
    """
    from medusa.modules.loader import load_local_module
    kg = load_local_module("knowledge_graph")

    if payload:
        result = kg.check_payload(target, payload)
        if result.get("blocked"):
            return f"⛔ BLOCKED: {result['reason']} (confidence: {result.get('confidence', 1.0):.0%})"
        return f"✅ Payload not in any known blocked pattern for {target}."
    else:
        return kg.summary(target)


def record_finding(target, finding_type, rule, evidence="", config=None):
    """Record a verified finding to the knowledge graph.

    Args:
        target:       hostname or IP
        finding_type: "blocks" | "rate_limit" | "waf" | "verified_cve" |
                      "false_positive" | "behavior" | "bypass"
        rule:         the constraint rule or finding description
        evidence:     what proved this finding
        config:       ignored
    """
    from medusa.modules.loader import load_local_module
    kg = load_local_module("knowledge_graph")

    valid_types = ("blocks", "rate_limit", "waf", "verified_cve",
                   "false_positive", "behavior", "bypass")
    if finding_type not in valid_types:
        return f"Invalid finding_type. Use one of: {', '.join(valid_types)}"

    kg.add_constraint(target, finding_type, rule, evidence=evidence or "",
                      confidence=1.0)
    return f"📝 Recorded: {target} → {finding_type} → '{rule}'"


# ----------------------------------------------------------------------
# Note-taking — timestamped engagement log
# ----------------------------------------------------------------------
NOTES_DIR = BASE_DIR / ".notes"


def write_note(content, success=True, category="general", engagement=None, config=None):
    """Write a timestamped note to a per-engagement log file.

    Each engagement gets its own file so findings don't bleed between runs.
    If no engagement name is given, one is auto-generated from the current date.

    Args:
        content:    the note body — what you did, tried, results, snippets
        success:    True/False — was this step successful?
        category:   "recon", "exploit", "cve", "oracle", "blocked", etc.
        engagement: name of the engagement (e.g. "cloudmart"). Creates .notes/{engagement}_notes.md.
                    If None, auto-generates from today's date: .notes/{date}_notes.md
        config:     ignored
    """
    import datetime
    NOTES_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    status = "✅ SUCCESS" if success else "❌ FAILED"

    # Determine the engagement file name
    if engagement:
        safe_name = re.sub(r"[^\w-]", "_", str(engagement).strip())[:48]
        filename = f"{safe_name}_notes.md"
    else:
        datestamp = now.strftime("%Y-%m-%d")
        filename = f"{datestamp}_notes.md"

    note_file = NOTES_DIR / filename

    header = (
        f"\n---\n"
        f"### {timestamp} — {status}\n"
        f"**Category:** {category}\n\n"
    )
    entry = header + content.strip() + "\n"

    if not note_file.exists():
        head = (
            f"# Medusa Engagement Notes — {filename.replace('_notes.md','')}\n"
            f"Started: {timestamp}\n"
            f"---\n"
        )
        note_file.write_text(head + entry, encoding="utf-8")
    else:
        with note_file.open("a", encoding="utf-8") as f:
            f.write(entry)

    return f"📒 Note written to .notes/{filename} [{category}] — {status}"


def route_tool(tool_name, args, config):
    if args is None: args = {}
    routes = {
        "execute_terminal": lambda a: execute_terminal(a.get("cmd") or a.get("command"), timeout=int(a.get("timeout", 30))),
        "search_kb": lambda a: search_kb(a.get("keyword")),
        "http_request": lambda a: http_request(a.get("method", "GET"), a.get("url"), a.get("headers"), a.get("body")),
        "read_file": lambda a: read_file(a.get("file_path", "")),
        "write_file": lambda a: write_file(a.get("file_path", ""), a.get("content", "")),
        "apply_patch": lambda a: apply_patch(a.get("vulnerability"), a.get("file_path", "lab.py")),
        "claim_flag": lambda a: f"OBJECTIVE MET: {a.get('flag')}",
        # Metasploit tools
        "msf_check": lambda a: msf_check(config),
        "msf_command": lambda a: msf_command(a.get("cmd") or a.get("command"), config),
        "msf_run": lambda a: msf_run(a.get("module"), a.get("payload"), a.get("options") or {}, config),
        "msf_sessions": lambda a: msf_sessions(a.get("action", "list"), a.get("id"), config),
        # CVE / vulnerability intelligence
        "search_cve": lambda a: search_cve(a.get("software"), config, version=a.get("version"), limit=int(a.get("limit", 5))),
        # Oracle / knowledge graph
        "check_knowledge": lambda a: check_knowledge(a.get("target"), payload=a.get("payload"), config=config),
        "record_finding": lambda a: record_finding(a.get("target"), a.get("finding_type"), a.get("rule"), evidence=a.get("evidence", ""), config=config),
        # Note-taking
        "write_note": lambda a: write_note(a.get("content", ""), success=a.get("success", True), category=a.get("category", "general"), engagement=a.get("engagement"), config=config),
        # Web search & self-improvement
        "web_search": lambda a: _web_search(a.get("query", ""), int(a.get("max_results", 5))),
        "edit_skill": lambda a: _edit_skill(a.get("skill_name", ""), a.get("new_content", "")),
        "write_tool": lambda a: _write_tool(a.get("tool_name", ""), a.get("code", "")),
        "list_skills": lambda a: _list_skills(),
        "list_own_files": lambda a: _list_own_files(),
        "pip_install": lambda a: _pip_install(a.get("package", "")),
        # Background job management
        "job_status": lambda a: _job_status(a.get("job_id", "")),
        "job_wait": lambda a: _job_wait(a.get("job_id", ""), a.get("timeout", 60)),
        "job_output": lambda a: _job_output(a.get("job_id", "")),
        "job_list": lambda a: _job_list(),
        "job_cancel": lambda a: _job_cancel(a.get("job_id", "")),
        # Analysis & reporting
        "payload_generate": lambda a: _payload_gen(a.get("vuln_type", ""), a.get("framework", "")),
        "diff_response": lambda a: _diff_resp(a.get("baseline", ""), a.get("injected", ""), a.get("sensitivity", "medium")),
        "rate_limit_check": lambda a: _rate_check(a.get("endpoint", "")),
        "rate_limit_all": lambda a: _rate_all(),
        "attack_tree": lambda a: _attack_tree(a.get("trace_json", "")),
        "generate_report": lambda a: _gen_report(a.get("engagement", ""), a.get("trace_json", ""), a.get("findings_json", "")),
        # deploy_subagent is an ACTION, not a tool. If the AI accidentally uses
        # it as a tool_name, show EXACTLY how to fix it so it self-corrects.
        "deploy_subagent": lambda a: (
            "WRONG FORMAT. deploy_subagent is an ACTION type, not a tool_name.\n"
            "You used: {\"action\": \"use_tool\", \"tool_name\": \"deploy_subagent\", ...}\n"
            "USE INSTEAD: {\"action\": \"deploy_subagent\", \"subagent_task\": \"your task\", \"thought\": \"...\"}\n"
            "Separate multiple tasks with || for parallel execution.\n"
            "Example: {\"action\": \"deploy_subagent\", \"subagent_task\": \"SQLi test on /login || XSS test on /search\", \"thought\": \"parallel attacks\"}"
        ),
    }
    # Inject module tools dynamically
    for t_name, t_func in get_module_tools().items():
        routes[t_name] = lambda a, f=t_func: f(**a)

    # ── FREEDOM: no phase gating. All tools always available. ──

    # Track recon actions (for informational purposes only)
    RECON_TOOLS = {
        "execute_terminal", "http_request", "search_cve", "search_kb",
        "read_file", "check_knowledge",
    }
    if tool_name in RECON_TOOLS:
        _recon_state["exploration_count"] = _recon_state.get("exploration_count", 0) + 1

    if tool_name in routes:
        try: return routes[tool_name](args)
        except Exception as e: return f"Routing Error: {str(e)}"
    return f"Invalid Tool: {tool_name}"


# ── New tools: web search, self-improvement, package install ──────────

def _web_search(query: str, max_results: int = 5) -> str:
    from medusa.tools.web_search import web_search
    return web_search(query, max_results)


def _edit_skill(skill_name: str, new_content: str) -> str:
    from medusa.tools.self_improve import edit_skill
    return edit_skill(skill_name, new_content)


def _write_tool(tool_name: str, code: str) -> str:
    from medusa.tools.self_improve import write_tool
    return write_tool(tool_name, code)


def _list_skills() -> str:
    from medusa.tools.self_improve import list_available_skills
    return list_available_skills()


def _list_own_files() -> str:
    from medusa.tools.self_improve import list_own_files
    return list_own_files()


def _pip_install(package: str) -> str:
    """Install a Python package for the agent to use. Requires confirmation."""
    if not package or not package.strip():
        return "Error: No package specified."
    safe = package.strip().split()[0]  # Only take first word for safety
    dangerous = {"os", "sys", "subprocess", "shutil", "importlib", "__builtins__"}
    if safe.lower() in dangerous:
        return f"Cannot install system module: {safe}"
    try:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", safe],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return f"✅ Installed {safe}\n{result.stdout[-500:]}"
        return f"❌ Failed to install {safe}\n{result.stderr[-500:]}"
    except Exception as e:
        return f"pip install error: {e}"


# ── Analysis & reporting tools ────────────────────────────────────────

def _payload_gen(vuln_type: str, framework: str = "") -> str:
    from medusa.tools.payload_generator import generate_payloads, list_payload_types
    if not vuln_type:
        return list_payload_types()
    return generate_payloads(vuln_type, framework=framework)


def _diff_resp(baseline: str, injected: str, sensitivity: str = "medium") -> str:
    from medusa.tools.diff_engine import diff_responses, quick_diff
    if len(baseline) < 200 and "http" not in baseline.lower():
        return quick_diff(baseline, injected)
    import json
    return json.dumps(diff_responses(baseline, injected, sensitivity), indent=2)


def _rate_check(endpoint: str) -> str:
    from medusa.tools.rate_limit_detector import check_rate_limit
    import json
    return json.dumps(check_rate_limit(endpoint), indent=2)


def _rate_all() -> str:
    from medusa.tools.rate_limit_detector import get_all_endpoints_status
    return get_all_endpoints_status()


def _attack_tree(trace_json: str) -> str:
    from medusa.tools.attack_tree import build_attack_tree
    import json
    trace = json.loads(trace_json) if trace_json else []
    return build_attack_tree(trace)


def _gen_report(engagement: str, trace_json: str, findings_json: str) -> str:
    from medusa.tools.report_exporter import generate_report
    import json
    trace = json.loads(trace_json) if trace_json else []
    findings = json.loads(findings_json) if findings_json else []
    return generate_report(engagement, trace, findings, {}, [], 0)


# ── Background job management ─────────────────────────────────────────

def _job_status(job_id: str) -> str:
    from medusa.nodes.execute_tool_node import _jobs as node_jobs, _job_lock as node_lock
    with node_lock:
        j = node_jobs.get(job_id)
    if not j:
        return f"Job {job_id} not found."
    elapsed = time.time() - j["started_at"]
    return (
        f"Job {job_id}: {j['status']} ({elapsed:.0f}s)\n"
        f"  Tool: {j['tool_name']}\n"
        f"  Args: {str(j.get('tool_args', {}))[:200]}\n"
        + (f"  Output: {str(j.get('output', ''))[:500]}" if j.get('output') else "  (no output yet)")
    )


def _job_wait(job_id: str, timeout: int = 60) -> str:
    from medusa.nodes.execute_tool_node import _jobs as node_jobs, _job_lock as node_lock
    deadline = time.time() + int(timeout)
    while time.time() < deadline:
        with node_lock:
            j = node_jobs.get(job_id)
        if not j:
            return f"Job {job_id} not found."
        if j["status"] in ("done", "failed", "cancelled"):
            return _job_status(job_id)
        time.sleep(1)
    return f"Job {job_id} still running after {timeout}s. Check job_status later."


def _job_output(job_id: str) -> str:
    from medusa.nodes.execute_tool_node import _jobs as node_jobs, _job_lock as node_lock
    with node_lock:
        j = node_jobs.get(job_id)
    if not j:
        return f"Job {job_id} not found."
    out = j.get("output", "")
    if not out:
        return f"Job {job_id}: {j['status']} — no output yet."
    return f"Job {job_id} output ({len(out)} chars):\n{out[:4000]}"


def _job_list() -> str:
    from medusa.nodes.execute_tool_node import _jobs as node_jobs, _job_lock as node_lock
    with node_lock:
        jobs = list(node_jobs.values())
    if not jobs:
        return "No background jobs."
    lines = []
    for j in jobs:
        elapsed = time.time() - j["started_at"]
        lines.append(f"  {j['job_id']}: {j['status']} ({elapsed:.0f}s) — {j['tool_name']}")
    return "Background jobs:\n" + "\n".join(lines)


def _job_cancel(job_id: str) -> str:
    from medusa.nodes.execute_tool_node import _jobs as node_jobs, _job_lock as node_lock
    with node_lock:
        j = node_jobs.get(job_id)
    if not j:
        return f"Job {job_id} not found."
    j["status"] = "cancelled"
    return f"Job {job_id} cancelled."


def get_tool_catalog():
    """Return a formatted catalog of ALL available tools for the AI's system prompt.

    Dynamically includes core tools, Metasploit, CVE search, Oracle, notes,
    and any loaded module tools. Called from redteamer to build the prompt.
    """
    from medusa.modules.loader import get_loaded_modules

    catalog = ""

    # ── MUST-USE TOOLS (these are NOT optional) ─────────────────────
    catalog += """##  MANDATORY TOOLS — Use These Every Turn
- **write_note** — MANDATORY after EVERY action. Log what you did, what happened, and what you learned. Categories: recon, exploit, cve, blocked, finding, progress, complete. Your audit trail and final report depend on these notes. DO NOT SKIP.
  ```json
  {"tool": "write_note", "args": {"content": "Tested SQLi on /login with payload ' OR 1=1 --. Login bypass confirmed. Gained admin session.", "success": true, "category": "finding", "engagement": "target-name"}}
  ```
- **check_knowledge** — QUERY THE KNOWLEDGE GRAPH before EVERY payload attempt. It stores verified blocked patterns, WAF rules, and successful exploit vectors. Stop wasting cycles on known-blocked payloads.
  ```json
  {"tool": "check_knowledge", "args": {"target": "TARGET_HOST"}}
  ```
- **record_finding** — WRITE TO THE KNOWLEDGE GRAPH after EVERY confirmed result. SQLi works? Record it. WAF blocked something? Record it. CVE confirmed? Record it. This prevents duplicate work and builds institutional knowledge.
  ```json
  {"tool": "record_finding", "args": {"target": "TARGET", "finding_type": "verified_cve", "rule": "CVE-2021-41773 path traversal works on /cgi-bin/.%2e/%2e%2e/etc/passwd", "evidence": "Got /etc/passwd contents in response"}}
  ```
- **generate_report** — MANDATORY at engagement end. Creates detailed Markdown report with all findings, attack chains, Mermaid diagrams. Call BEFORE complete/claim_flag.
  ```json
  {"tool": "generate_report", "args": {"engagement": "target-name"}}
  ```

## Core Tools
- **execute_terminal** — Run ANY shell command. Use this for CLI tools: nmap, gobuster, ffuf, nikto, sqlmap, hydra, john, enum4linux, dirb, masscan, and any other pentesting tool installed on the system. Prefer dedicated CLI tools over raw curl/http_request for scanning and brute-forcing.
  ```json
  {"tool": "execute_terminal", "args": {"cmd": "gobuster dir -u http://TARGET -w /usr/share/wordlists/dirb/common.txt"}}
  {"tool": "execute_terminal", "args": {"cmd": "nmap -sV -sC TARGET"}}
  ```
- **http_request** — Raw HTTP requests with full browser emulation. Use for manual web testing, not for scanning (use gobuster/nmap via execute_terminal instead).
  ```json
  {"tool": "http_request", "args": {"method": "GET", "url": "http://TARGET/page"}}
  ```
- **read_file** — Read any file on the system.
  ```json
  {"tool": "read_file", "args": {"file_path": "/etc/hosts"}}
  ```
- **write_file** — Write files (scripts, payloads, notes). Defaults to medusa_agent/ for relative paths.
  ```json
  {"tool": "write_file", "args": {"file_path": "scripts/exploit.py", "content": "#!/usr/bin/env python3\\n..."}}
  ```
- **search_kb** — Search the local knowledge base.
  ```json
  {"tool": "search_kb", "args": {"keyword": "SQL injection"}}
  ```
- **apply_patch** — Patch vulnerabilities in the target lab application.
  ```json
  {"tool": "apply_patch", "args": {"vulnerability": "sqli"}}
  ```
- **claim_flag** — Signal objective complete.
  ```json
  {"tool": "claim_flag", "args": {"flag": "flag{...}"}}
  ```

## Metasploit
- **msf_check** — Verify Metasploit availability.
  ```json
  {"tool": "msf_check", "args": {}}
  ```
- **msf_command** — Run raw msfconsole commands.
  ```json
  {"tool": "msf_command", "args": {"cmd": "search eternalblue"}}
  ```
- **msf_run** — Execute exploit/auxiliary/post modules.
  ```json
  {"tool": "msf_run", "args": {"module": "exploit/multi/handler", "payload": "windows/meterpreter/reverse_tcp", "options": {"LHOST": "10.0.0.5", "LPORT": "4444"}}}
  ```
- **msf_sessions** — Manage sessions.
  ```json
  {"tool": "msf_sessions", "args": {"action": "list"}}
  ```

## Intelligence
- **search_cve** — Query NVD for CVEs by software+version.
  ```json
  {"tool": "search_cve", "args": {"software": "apache httpd", "version": "2.4.49", "limit": 5}}
  ```
- **check_knowledge** — Query the knowledge graph before generating payloads.
  ```json
  {"tool": "check_knowledge", "args": {"target": "TARGET"}}
  ```
- **record_finding** — Persist verified findings.
  ```json
  {"tool": "record_finding", "args": {"target": "TARGET", "finding_type": "blocks", "rule": "' OR 1=1", "evidence": "WAF 403"}}
  ```
- **write_note** — Log engagement progress.
  ```json
  {"tool": "write_note", "args": {"content": "Progress update...", "success": true, "category": "progress", "engagement": "target-name"}}
  ```

## Creative Freedom Tools
- **web_search** — Search the internet for exploit techniques, CVE details, documentation.
  ```json
  {"tool": "web_search", "args": {"query": "apache 2.4.49 CVE exploit", "max_results": 5}}
  ```
- **pip_install** — Install Python packages the agent needs (requests, pwntools, etc).
  ```json
  {"tool": "pip_install", "args": {"package": "requests"}}
  ```
- **edit_skill** — Improve your own hacking methodology by editing skill prompts.
  ```json
  {"tool": "edit_skill", "args": {"skill_name": "sql_injection", "new_content": "..."}}
  ```
- **write_tool** — Create new Python tools to extend your capabilities.
  ```json
  {"tool": "write_tool", "args": {"tool_name": "my_scanner", "code": "def scan():..."}}
  ```
- **list_skills** — See all attack skills you can edit.
  ```json
  {"tool": "list_skills", "args": {}}
  ```
- **list_own_files** — See all code files you can read and modify.
  ```json
  {"tool": "list_own_files", "args": {}}
  ```

## Background Jobs (parallel execution)
- **job_spawn** happens automatically for slow tools (nmap, gobuster, sqlmap, hydra, ffuf, nikto).
  When you run these via execute_terminal, they return a job_id immediately. You keep working!
- **job_status** — Check status of a background job.
  ```json
  {"tool": "job_status", "args": {"job_id": "abc123"}}
  ```
- **job_wait** — Wait for a job to complete (with timeout).
  ```json
  {"tool": "job_wait", "args": {"job_id": "abc123", "timeout": 60}}
  ```
- **job_output** — Get full output from a completed job.
  ```json
  {"tool": "job_output", "args": {"job_id": "abc123"}}
  ```
- **job_list** — List all running background jobs.
  ```json
  {"tool": "job_list", "args": {}}
  ```
- **job_cancel** — Cancel a running job.
  ```json
  {"tool": "job_cancel", "args": {"job_id": "abc123"}}
  ```
"""

    # Module tools
    modules = get_loaded_modules()
    if modules:
        catalog += "## Module Tools\n"
        for mod_name, mod_data in modules.items():
            manifest = mod_data.get("manifest", {})
            tools = manifest.get("tools", {})
            deps = manifest.get("dependencies", [])
            if tools:
                catalog += f"### {mod_name}"
                if deps:
                    catalog += f" (requires: {', '.join(deps)})"
                catalog += "\n"
                for t_name, t_info in tools.items():
                    desc = t_info.get("description", "")
                    params = t_info.get("parameters", {})
                    param_example = ", ".join(f'"{p}": "..."' for p in params)
                    catalog += f"- **{t_name}** — {desc}\n"
                    catalog += f"  ```json\n"
                    if param_example:
                        catalog += f'  {{"tool": "{t_name}", "args": {{{param_example}}}}}\n'
                    else:
                        catalog += f'  {{"tool": "{t_name}", "args": {{}}}}\n'
                    catalog += f"  ```\n"

    # Strategy reminder
    catalog += """
## Attack Strategy (MUST FOLLOW)
1. **Recon first** — Always start with `execute_terminal` running gobuster/nmap/nikto before manual testing. Never start with raw curl.
2. **CVE before exploit** — `search_cve` after fingerprinting a service. Don't guess.
3. **Knowledge graph before payload** — `check_knowledge` before every new payload.
4. **Verify before claiming** — Confirm exploits with tool-call evidence. No hallucinations.
5. **Log everything** — `write_note` after every significant finding.
6. **Module tools** — Use loaded module tools (above) when applicable instead of reinventing.
"""
    return catalog
