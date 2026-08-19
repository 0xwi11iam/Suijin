import requests

_T = (3, 8)


def bucket_check(name: str = "") -> str:
    if not name:
        return "Error: name required (e.g. 'acme' from acme.com)"
    base = name.strip().lower().replace("http://", "").replace("https://", "").split(".")[0]
    candidates = [base, f"{base}-backups", f"{base}-dev", f"{base}-staging", f"{base}-prod", f"{base}-assets", f"{base}-media", f"{base}-data"]
    urls = []
    for c in candidates:
        urls += [
            f"https://{c}.s3.amazonaws.com/",
            f"https://storage.googleapis.com/{c}",
            f"https://{c}.blob.core.windows.net/$web",
            f"https://{c}.public.blob.core.windows.net/$web",
        ]
    found = []
    for u in urls[:32]:
        try:
            r = requests.get(u, timeout=_T, headers={"User-Agent": "suijin-bucket-probe"})
            if r.status_code == 200 and ("<ListBucketResult" in r.text[:2000] or "Items" in r.text[:500] or "EnumerationResults" in r.text[:2000]):
                found.append(f"PUBLIC LISTING {u}")
            elif r.status_code == 200 and len(r.content) > 0:
                found.append(f"public object {u} ({len(r.content):,}B)")
            elif r.status_code == 403:
                found.append(f"exists-private {u}")
        except requests.RequestException:
            continue
    return "\n".join(found[:20]) if found else f"No buckets found for '{base}' across {len(urls)} candidates (S3/GCS/Azure)."
