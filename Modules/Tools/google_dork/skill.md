# Google Dorking

Search target via DuckDuckGo with Google dork syntax.

```json
{"tool": "dork_search", "args": {"query": "site:target.com inurl:admin", "max_results": 10}}
```

Built-in dork templates: `site:TARGET inurl:admin`, `site:TARGET filetype:sql`, `site:TARGET intitle:"index of"`, `site:TARGET intext:"password"`.