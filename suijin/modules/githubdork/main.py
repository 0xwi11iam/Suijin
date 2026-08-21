import requests


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:  # standalone fallback
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


_T = (5, 20)
_UA = {"User-Agent": "_stealth_ua()"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


def github_dork_urls(org: str = "") -> str:
    if not org:
        return "Error: org required"
    o = org.strip()
    dorks = [
        "filename:.env",
        "filename:config.php",
        "filename:id_rsa",
        "filename:.npmrc",
        "filename:credentials",
        "filename:secrets.yml",
        "filename:.git-credentials",
        "extension:pem private",
        "extension:sql password",
        "ORG api_key",
        "ORG aws_secret",
        "ORG sendgrid",
        "ORG slack_token",
        "filename:wp-config.php",
    ]
    urls = [f"https://github.com/search?q={d.replace('ORG', o).replace(' ', '+')}&type=code" for d in dorks]
    return "Open in a browser (search needs auth):\n  " + "\n  ".join(urls)


def github_gist_scan(user: str = "") -> str:
    if not user:
        return "Error: user required"
    try:
        r = _get(f"https://api.github.com/users/{user.strip()}/gists")
        if r.status_code == 403:
            return "rate-limited — retry in a bit or add a token header via http_request"
        gists = r.json()
    except (requests.RequestException, ValueError) as e:
        return f"Error: {e}"
    if not gists:
        return f"No public gists for {user}"
    out = []
    for g in gists[:15]:
        files = list(g.get("files") or {})[:5]
        out.append(f"{g.get('html_url')} ({', '.join(files)})")
    return "\n".join(out)
