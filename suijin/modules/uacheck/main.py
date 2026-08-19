_BOTS = {
    "bot": "BOT/crawler",
    "spider": "BOT/crawler",
    "crawl": "BOT/crawler",
    "slurp": "BOT (Yahoo)",
    "googlebot": "BOT (Google)",
    "bingpreview": "BOT (Bing)",
    "curl/": "curl",
    "wget": "wget",
    "python-requests": "python-requests",
    "go-http": "Go http",
    "okhttp": "okHttp (mobile app)",
    "java/": "Java",
    "scrapy": "Scrapy",
    "nmap": "nmap scripting engine",
    "masscan": "masscan",
    "sqlmap": "sqlmap (ATTACKER TOOL)",
    "nikto": "nikto (ATTACKER TOOL)",
    "gobuster": "gobuster (ATTACKER TOOL)",
}
_BROWSERS = {
    "firefox/": "Firefox",
    "chrome/": "Chrome",
    "safari/": "Safari",
    "edg/": "Edge",
    "msie": "IE",
    "trident/": "IE",
}
_OSES = {
    "windows nt": "Windows",
    "macintosh": "macOS",
    "android": "Android",
    "iphone": "iOS",
    "ipad": "iPadOS",
    "linux": "Linux",
    "x11": "X11",
}


def parse_user_agent(ua: str = "") -> str:
    if not ua:
        return "Error: ua string required"
    low = ua.strip().lower()
    tags = [label for token, label in _BOTS.items() if token in low]
    if not tags:
        tags = [label for token, label in _BROWSERS.items() if token in low] or ["unknown browser"]
    os_hits = [label for token, label in _OSES.items() if token in low]
    parts = [f"{len(ua)} chars -> " + ", ".join(dict.fromkeys(tags))]
    if os_hits:
        parts.append("OS: " + ", ".join(dict.fromkeys(os_hits)))
    if any("ATTACKER" in t for t in tags):
        parts.append("NOTE: attack-tool UA — this string is a red flag in blue-team logs")
    return " | ".join(parts)
