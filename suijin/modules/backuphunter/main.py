
import requests

_T = (5, 20)
_UA = {"User-Agent": "Mozilla/5.0 (suijin recon)"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


_EXTS = (".bak", ".old", ".orig", ".save", ".swp", "~", ".txt", ".zip", ".tar.gz", ".7z", ".gz", ".copy", ".default")


def backup_file_probe(url: str = "", path: str = "index.php") -> str:
    if not url:
        return "Error: url required"
    base = url.strip().rstrip("/") + "/" + (path.strip("/") or "index.php")
    stem = base.rsplit(".", 1)[0]
    cands = [base + e for e in _EXTS] + [stem + e for e in _EXTS] + [base + ".php.bak", stem + ".2024", base.replace(".", "_")]
    found = []
    for u in cands[:26]:
        try:
            r = _get(u)
            if r.status_code == 200 and len(r.content) > 20 and r.status_code == 200:
                ct = r.headers.get("content-type", "")
                if "text/html" in ct and len(r.content) < 200 and "404" in r.text[:200]:
                    continue
                found.append(f"{u} ({len(r.content):,}B, {ct.split(';')[0]})")
        except requests.RequestException:
            continue
    return "\n".join(found[:15]) if found else "No backup/archive files found for that path."
