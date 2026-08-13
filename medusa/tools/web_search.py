"""
Web search tool — gives the agent internet access to research CVEs,
exploit techniques, documentation, and current attack methods.

Uses DuckDuckGo HTML search (no API key needed) as a free fallback.
"""
from __future__ import annotations
import re
import urllib.parse
import requests

_user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


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
        # Parse DuckDuckGo Lite results — they use <a> tags with class="result-link"
        results = []
        # Pattern: <a rel="nofollow" class="result-link" href="URL">Title</a>
        # followed by <td class="result-snippet">Snippet</td>
        link_pattern = re.compile(
            r'<a[^>]*class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        snippet_pattern = re.compile(
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            re.DOTALL | re.IGNORECASE,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (url, title) in enumerate(links[:max_results]):
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            url_clean = urllib.parse.unquote(url.split("//")[-1].split("/uddg/")[1] if "/uddg/" in url else url)
            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()[:300]

            results.append(f"{i+1}. **{title_clean}**\n   {snippet}\n   {url_clean}")

        if not results:
            return f"No results found for: {query}"

        return f"## Web Search: {query}\n\n" + "\n\n".join(results)

    except requests.Timeout:
        return "Search error: Request timed out."
    except Exception as e:
        return f"Search error: {e}"
