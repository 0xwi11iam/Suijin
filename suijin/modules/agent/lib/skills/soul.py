"""
SOUL Skill — Browser-first web application testing.
Explicitly instructs the AI to use Playwright browser MCP for
JavaScript-heavy SPAs, React/Remix/Next.js apps, and any page
where curl/http_request returns empty or incomplete content.
"""

SOUL_SKILL_PROMPT = """
## ATTACK SKILL: BROWSER-FIRST WEB TESTING (SOUL)

**THIS IS THE DEFAULT APPROACH FOR MODERN WEB APPS.**
**For any JavaScript-heavy target (React, Remix, Next.js, Vue, Angular, Svelte,
or any SPA), you MUST use the browser MCP tools as your PRIMARY interaction
method. Do NOT rely on curl/http_request alone for JS-rendered pages.**

---

### WHEN TO USE THE BROWSER (ALWAYS FOR THESE):

1. **The target is a Single Page Application (SPA)** — React, Vue, Angular, Svelte, Next.js, Remix, Nuxt. These return empty `<div id="root">` or `<div id="app">` shells. curl/http_request will give you empty HTML. You MUST use the browser.

2. **curl/http_request returns suspiciously small HTML** — If the response body is under 500 bytes and has no meaningful content, the site requires JavaScript. Switch to browser IMMEDIATELY.

3. **The page has CSP headers with connect-src, script-src pointing to subdomains** — This indicates API backends and third-party services. Use the browser's mcp_browser_exec to inspect `window` objects and network requests.

4. **Login forms with CSRF tokens, nonces, or dynamic IDs** — These change per request and cannot be predicted. You need snapshot + type + click, not raw HTTP.

5. **Any OAuth, SAML, or 2FA flow** — These require redirect following, popup handling, and JavaScript execution. curl cannot handle them.

6. **Cloudflare-protected sites** — Cloudflare challenges require JavaScript execution. The browser MCP handles this automatically.

---

### BROWSER TOOL REFERENCE

| Tool | Purpose |
|------|---------|
| `mcp_browser_goto` | Navigate to a URL |
| `mcp_browser_snapshot` | List ALL interactive elements (buttons, inputs, links) with [N] index numbers |
| `mcp_browser_click` | Click element by index [N], CSS selector, or visible text |
| `mcp_browser_type` | Type text into an input by index [N] or selector |
| `mcp_browser_screenshot` | Capture full-page screenshot (saves to /tmp/) |
| `mcp_browser_extract` | Get visible text from the page body or a specific element |
| `mcp_browser_exec` | Run arbitrary JavaScript in the page context |
| `mcp_browser_get_html` | Get the full rendered HTML (after JS execution) |

---

### BROWSER WORKFLOW FOR RECON

```
1. mcp_browser_goto {url: "https://target.com"}
2. mcp_browser_snapshot {} -> see all clickable elements
3. mcp_browser_extract {selector: "body"} -> read page content
4. Navigate to /login, /register, /admin, /api (if linked)
5. Snapshot each page, note: form fields, hidden inputs, API endpoints in links
6. mcp_browser_exec {js_code: "window.location.origin"} -> get base URL
7. mcp_browser_exec {js_code: "document.cookie"} -> check for session tokens
```

### BROWSER WORKFLOW FOR EXPLOITATION

```
1. Navigate to the target form/page
2. Snapshot to see input fields and their indices
3. Click an input to focus it: mcp_browser_click {selector: "2"}
4. Type payload: mcp_browser_type {selector: "2", text: "' OR '1'='1"}
5. Click submit: mcp_browser_click {selector: "5"} (the submit button)
6. Snapshot again to see the response
7. Extract text to read error messages, flag output, or response content
8. Screenshot for evidence: mcp_browser_screenshot {}
```

### WHAT THE BROWSER SEES THAT CURL DOESN'T

- Rendered DOM after JavaScript execution
- WebSocket connections (check Network tab via JS)
- localStorage, sessionStorage, IndexedDB
- Cookies set by JavaScript (HttpOnly false)
- Dynamic form fields added by JS
- Client-side API calls (fetch/XHR to backend endpoints)
- CSP violations, CORS errors, mixed content warnings

### RULES

- **FOR SPAs: ALWAYS start with the browser.** curl/http_request is for API testing only after you've discovered the API endpoints via the browser.
- **Snapshot before every action** — element indices change between pages.
- **Click inputs before typing** — the page needs focus events to work.
- **If a click or type fails**, try the index number [N] instead of text.
- **After submitting a form**, snapshot and extract to verify the result.
- **Screenshot critical findings** for the engagement report.
"""
