# MCP Playwright — AI Browser Automation

Headless Chromium browser for JS-rendered pages, SPA interaction, form filling, screenshot capture.

## WORKFLOW

1. **Navigate** to the target page.
2. **Snapshot** to see all interactive elements with index numbers.
3. **Click/Type** by referencing snapshot indices (most reliable).
4. **Extract** text or **Screenshot** to capture results.
5. **Close** the browser when done.

## TOOL REFERENCE

### Navigate
```json
{"tool": "mcp_browser_goto", "args": {"url": "https://target.com/login"}}
```

### Snapshot (GET INTERACTIVE ELEMENTS — use this BEFORE clicking/typing)
```json
{"tool": "mcp_browser_snapshot", "args": {}}
```
Returns a numbered list like:
```
  [  1] BUTTON  "Log in"              
  [  2] INPUT   "Email"        type=email
  [  3] INPUT   "Password"     type=password
  [  4] LINK    "Forgot password?"
```
Use the index numbers [N] in click/type commands.

### Click (by index, selector, or text)
```json
{"tool": "mcp_browser_click", "args": {"selector": "1"}}
{"tool": "mcp_browser_click", "args": {"selector": "#login-btn"}}
{"tool": "mcp_browser_click", "args": {"selector": "Log in"}}
```

### Type (by index or selector)
```json
{"tool": "mcp_browser_type", "args": {"selector": "2", "text": "admin@test.com"}}
{"tool": "mcp_browser_type", "args": {"selector": "3", "text": "password123"}}
```

### Screenshot
```json
{"tool": "mcp_browser_screenshot", "args": {}}
```
Saves to /tmp/medusa_screenshot.png

### Extract Text
```json
{"tool": "mcp_browser_extract", "args": {"selector": "body"}}
{"tool": "mcp_browser_extract", "args": {"selector": "#error-message"}}
```

### Execute JavaScript
```json
{"tool": "mcp_browser_exec", "args": {"js_code": "document.cookie"}}
```

### Get Full HTML
```json
{"tool": "mcp_browser_get_html", "args": {}}
```

### Close Browser
```json
{"tool": "mcp_browser_close", "args": {}}
```

## BEST PRACTICES

- ALWAYS call snapshot BEFORE clicking or typing to get current element indices.
- Click by index [N] is more reliable than text matching.
- After clicking, call snapshot again to see the new page state.
- For SPAs, use extract to get visible text rather than get_html (too large).
- Close the browser when done to free memory.

Install: `pip install playwright && playwright install chromium`