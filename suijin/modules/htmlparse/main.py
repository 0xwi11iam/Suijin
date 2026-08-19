import re
import urllib.parse


def extract_forms(html: str = "") -> str:
    if not html:
        return "Error: html required"
    forms = re.findall(r"<form\b[^>]*>(.*?)</form>", html, re.S | re.I)
    if not forms:
        return "No <form> elements found"
    out = []
    full_forms = re.findall(r"<form\b[^>]*>", html, re.I)
    for i, (tag, body) in enumerate(zip(full_forms, forms), 1):
        attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', tag))
        inputs = re.findall(r"<(?:input|textarea|select)\b[^>]*>", body, re.I)
        fields = []
        for inp in inputs:
            a = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', inp))
            if a.get("name"):
                fields.append(
                    f"{a.get('name')} ({a.get('type', 'text')}{', hidden' if a.get('type') == 'hidden' else ''}{', value=' + a.get('value', '')[:40] if a.get('value') else ''})"
                )
        out.append(
            f"form {i}: {attrs.get('method', 'GET')} {attrs.get('action', '(self)')} — {len(fields)} fields\n  "
            + "\n  ".join(fields)
        )
    return "\n".join(out)


def extract_links(html: str = "", base_url: str = "") -> str:
    if not html:
        return "Error: html required"
    raw = re.findall(r'(?:href|src|action)=["\']([^"\']+)["\']', html, re.I)
    uniq = []
    seen = set()
    for u in raw:
        if base_url and not u.startswith(("http", "//", "data:", "#")):
            u = urllib.parse.urljoin(base_url if "://" in base_url else "http://" + base_url, u)
        if u not in seen and not u.startswith(("javascript:", "data:", "#")):
            seen.add(u)
            uniq.append(u)
    if not uniq:
        return "No links found"
    interesting = [
        u
        for u in uniq
        if any(x in u.lower() for x in (".json", "api", "admin", "upload", ".bak", "config", "debug", "token", "key"))
    ]
    out = [f"{len(uniq)} links"]
    if interesting:
        out.append("interesting:\n  " + "\n  ".join(interesting[:30]))
    out.append("all:\n  " + "\n  ".join(uniq[:150]))
    return "\n".join(out)


def extract_comments(html: str = "") -> str:
    if not html:
        return "Error: html required"
    comments = re.findall(r"<!--(.*?)-->", html, re.S)
    keep = [
        c.strip()[:200] for c in comments if c.strip() and not c.startswith(("[if", "<![endif", "DOCTYPE", "do not"))
    ]
    return "\n---\n".join(keep[:30]) if keep else "No comments (or only boilerplate IE conditionals)"
