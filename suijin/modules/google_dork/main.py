"""Google Dorking via DuckDuckGo."""


def dork_search(query, max_results=10):
    if not query:
        return "Error: query required"
    try:
        from duckduckgo_search import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=int(max_results)):
                results.append(f"{r.get('title', '?')}\n{r.get('href', '?')}\n{r.get('body', '')[:200]}")
        return "\n\n".join(results) if results else "(no results)"
    except ImportError:
        return "Error: duckduckgo-search not installed. Run: pip install duckduckgo-search"
    except Exception as e:
        return f"Dork error: {e}"
