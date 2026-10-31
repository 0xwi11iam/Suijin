"""
Web search tool — gives the agent internet access to research CVEs,
exploit techniques, documentation, and current attack methods.

Uses DuckDuckGo HTML search (no API key needed) as a free fallback.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

import requests

_user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def web_fetch(url: str, max_chars: int = 8000) -> str:
    """Fetch a page as READABLE text (2026-10-31, opencode-versatility):
    scripts/styles/tags stripped, links kept as [text](href) — docs pages,
    PoC writeups, and advisory notes arrive as research material instead
    of HTML soup. Truncated at max_chars with a pointer."""
    if not url or not str(url).startswith(("http://", "https://")):
        return "Error: url required (http(s)://…)"
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": _user_agent})
        if resp.status_code != 200:
            return f"Fetch error: HTTP {resp.status_code}"
        html = resp.text
        html = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html)
        html = re.sub(r"(?is)<!--.*?-->", " ", html)
        # links become [text](href) before tags drop
        html = re.sub(
            r"(?is)<a[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
            lambda m: f"[{re.sub(r'<[^>]+>', '', m.group(2)).strip() or m.group(1)[:40]}]({m.group(1)})",
            html,
        )
        html = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", html)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[… truncated at {max_chars} chars — fetch the full page for more]"
        return f"[{resp.status_code}] {url}\n\n{text}"
    except Exception as e:  # noqa: BLE001 — network errors are data
        return f"Fetch error: {e}"


def fetch_resource(url: str, save_to: str = "", max_bytes: int = 500_000_000) -> str:
    """Download an online resource to a REAL local path (2026-10-30
    ruling: online resources are installable). save_to defaults to the
    engagement home with the URL's filename. System-floor paths are
    refused; local_access passes real paths through. Integrity: size +
    sha256 reported so the agent can verify before installing."""
    import hashlib

    if not url or not str(url).startswith(("http://", "https://")):
        return "Error: url required (http(s)://…)"

    from suijin.server.confinement import is_system_path, jail_path

    if is_system_path(save_to or "x"):
        return "Error: system paths are not writable (the floor)"
    target = Path(str(jail_path(save_to or "")))
    if not save_to:
        name = str(url).rstrip("/").split("/")[-1].split("?")[0] or "download.bin"
        from suijin.modules.platform.lib.workspace import home_dir

        target = Path(home_dir()) / "Downloads" / name
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        with requests.get(url, stream=True, timeout=60, headers={"User-Agent": _user_agent}) as r:
            if r.status_code != 200:
                return f"Download error: HTTP {r.status_code}"
            h = hashlib.sha256()
            size = 0
            with open(target, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        fh.close()
                        target.unlink(missing_ok=True)
                        return f"Error: exceeded {max_bytes} bytes — refusing"
                    h.update(chunk)
                    fh.write(chunk)
        return (
            f"Downloaded {size:,} bytes → {target}\nsha256: {h.hexdigest()}\n"
            f"Install as appropriate: pip_install (packages), execute_terminal (chmod +x / installer), "
            f"and verify the hash matches the source's published value before trusting it."
        )
    except Exception as e:  # noqa: BLE001 — network errors are data
        return f"Download error: {e}"


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return results.

    Uses DuckDuckGo Lite (no API key required). Returns titles, snippets,
    and URLs for the top results.

    Args:
        query: Search query string.
        max_results: Maximum results to return (default 5).

    Returns:
        Formatted search results string.
    """
    if not query:
        return "Error: No search query provided."

    try:
        url = "https://lite.duckduckgo.com/lite/"
        headers = {"User-Agent": _user_agent, "Content-Type": "application/x-www-form-urlencoded"}
        data = {"q": query}

        resp = requests.post(url, headers=headers, data=data, timeout=15)
        if resp.status_code != 200:
            return f"Search error: HTTP {resp.status_code}"

        html = resp.text
        # Parse DuckDuckGo Lite results. The markup drifts: class may use
        # single OR double quotes and href may precede or follow the class
        # attribute (the double-quote/class-first pattern silently returned
        # zero results for weeks). Match quote- and order-agnostically.
        results = []
        link_pattern = re.compile(
            r"<a[^>]*class=['\"]result-link['\"][^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>"
            r"|<a[^>]*href=['\"]([^'\"]+)['\"][^>]*class=['\"]result-link['\"][^>]*>(.*?)</a>",
            re.DOTALL | re.IGNORECASE,
        )
        snippet_pattern = re.compile(
            r"<t[d][^>]*class=['\"]result-snippet['\"][^>]*>(.*?)</t[d]>",
            re.DOTALL | re.IGNORECASE,
        )

        links = [(u1 or u2, t1 or t2) for (u1, t1, u2, t2) in link_pattern.findall(html)]
        snippets = snippet_pattern.findall(html)

        for i, (url, title) in enumerate(links[:max_results]):
            title_clean = re.sub(r"<[^>]+>", "", title).strip()
            url_clean = urllib.parse.unquote(url.split("//")[-1].split("/uddg/")[1] if "/uddg/" in url else url)
            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()[:300]

            results.append(f"{i + 1}. **{title_clean}**\n   {snippet}\n   {url_clean}")

        if not results:
            return f"No results found for: {query}"

        return f"## Web Search: {query}\n\n" + "\n\n".join(results)

    except requests.Timeout:
        return "Search error: Request timed out."
    except Exception as e:
        return f"Search error: {e}"
