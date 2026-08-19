# mitmproxy Traffic Capture

Transparent HTTP/HTTPS proxy for passive traffic capture during recon.

```json
{"tool": "mitm_start_capture", "args": {"port": 8080}}
{"tool": "mitm_analyze_flow", "args": {"filter": "api"}}
```

Configure browser: `Settings → Proxy → localhost:8080`. Install CA cert from `http://mitm.it`.