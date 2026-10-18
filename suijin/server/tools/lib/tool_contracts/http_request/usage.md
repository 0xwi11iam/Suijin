Makes an HTTP request to a target URL. Returns status, headers, body.

**Usage rules:**
- Always provide the FULL URL including scheme (http:// or https://)
- headers is a dict: `{"Authorization": "Bearer ...", "Content-Type": "application/json"}`
- body is a string for POST/PUT: `'{"key": "value"}'`
- For request comparison (baseline vs exploit), use `http_replay` with `compare` instead
- Large responses are capped; the full body auto-offloads to toollogs/
