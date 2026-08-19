import re

_RULES = [
    (
        "server",
        {
            "nginx": "nginx",
            "apache": "Apache",
            "microsoft-iis": "IIS",
            "litespeed": "LiteSpeed",
            "caddy": "Caddy",
            "cloudflare": "behind Cloudflare",
            "gunicorn": "Python/gunicorn",
            "werkzeug": "Python/werkzeug",
            "tomcat": "Java/Tomcat",
            "jetty": "Java/Jetty",
        },
    ),
    (
        "x-powered-by",
        {
            "php": "PHP",
            "asp.net": "ASP.NET",
            "express": "Node/Express",
            "servlet": "Java servlet",
            "next.js": "Next.js",
            "jsf": "JSF",
        },
    ),
    ("x-aspnet-version", {"": "ASP.NET (version leak)"}),
    ("x-generator", {"drupal": "Drupal", "wordpress": "WordPress", "joomla": "Joomla"}),
]
_BODY_MARKS = [
    ("wp-content", "WordPress"),
    ("wp-json", "WordPress REST"),
    ("/sites/default/", "Drupal"),
    ("JSESSIONID", "Java"),
    ("XSRF-TOKEN|laravel_session", "Laravel"),
    ("__VIEWSTATE", "ASP.NET WebForms"),
    ("next/static|_next/", "Next.js"),
    ("react", "React"),
    ("vue", "Vue"),
    ("angular", "Angular"),
    ("cgi-bin", "CGI"),
    ("joomla", "Joomla"),
    ("shopify", "Shopify"),
    ("struts", "Struts"),
    ("thinkphp", "ThinkPHP"),
    ("laravel", "Laravel"),
]


def fingerprint_headers(headers: dict = None, body: str = "") -> str:
    if not isinstance(headers, dict) or not headers:
        return "Error: headers dict required"
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    found = []
    for hdr, table in _RULES:
        if hdr in h:
            for token, label in table.items():
                if token and token in h[hdr].lower():
                    found.append(f"{hdr}: {h[hdr]} -> {label}")
                    break
            else:
                found.append(f"{hdr}: {h[hdr]}")
    for token, label in (
        ("jsessionid", "Java"),
        ("phpsessid", "PHP"),
        ("asp.net", "ASP.NET"),
        ("laravel_session", "Laravel"),
        ("csrftoken", "Django/DRF"),
    ):
        if token in h.get("set-cookie", "").lower():
            found.append(f"cookie marker: {token} -> {label}")
    if body:
        low = body[:4096]
        for pat, label in _BODY_MARKS:
            if re.search(pat.lower(), low, re.I):
                found.append(f"body marker: {label}")
    return (
        "Fingerprint:\n  "
        + ("\n  ".join(found) if found else "no stack markers — custom app or hardened front")
        + "\n(pair with whatweb_scan if installed)"
    )
