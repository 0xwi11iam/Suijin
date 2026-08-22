import re
import zipfile
from pathlib import Path

_URL = re.compile(r"https?://[\w.-]+[\w/.,%#?=&+-]{2,}")
_KEY = re.compile(
    r"(AIza[0-9A-Za-z_-]{35}|SK[0-9a-fA-F]{32}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)


def _iter_apk(apk_path: str):
    p = Path(apk_path).expanduser()
    if not p.is_file():
        return None, f"Error: {p} not found"
    try:
        return zipfile.ZipFile(p), None
    except zipfile.BadZipFile:
        return None, "Error: not a valid zip/apk"


def apk_inventory(apk_path: str = "") -> str:
    z, err = _iter_apk(apk_path)
    if err:
        return err
    names = z.namelist()
    dex = [n for n in names if n.endswith(".dex")]
    native = [n for n in names if n.endswith((".so",))]
    assets = [n for n in names if n.startswith("assets/")][:20]
    meta = [n for n in names if n.startswith("META-INF/")]
    interesting = [
        n for n in names if any(x in n.lower() for x in (".db", ".sqlite", ".json", "config", "secret", "key", "pass"))
    ][:20]
    return (
        f"{len(names)} entries | {len(dex)} dex | {len(native)} native libs\n"
        f"assets: {assets}\ninteresting files: {interesting or '-'}\n"
        f"signing: {[m for m in meta if m.endswith(('.RSA', '.SF', '.EC'))]}"
    )


def apk_strings(apk_path: str = "", pattern: str = "") -> str:
    z, err = _iter_apk(apk_path)
    if err:
        return err
    custom = re.compile(pattern) if pattern else None
    urls, keys, customs = set(), set(), set()
    for n in z.namelist():
        if not n.endswith((".dex", ".xml", ".json", ".properties", ".txt")):
            continue
        try:
            blob = z.read(n)[:2_000_000]
        except Exception:
            continue
        text = blob.decode("utf-8", "ignore")
        urls.update(_URL.findall(text))
        keys.update(_KEY.findall(text))
        if custom:
            customs.update(custom.findall(text))
    out = []
    if urls:
        out.append(f"URLs ({len(urls)}):\n  " + "\n  ".join(sorted(urls)[:40]))
    if keys:
        out.append(f"SECRETS ({len(keys)}):\n  " + "\n  ".join(sorted(keys)[:20]))
    if custom and customs:
        out.append(f"pattern hits ({len(customs)}):\n  " + "\n  ".join(str(c) for c in sorted(customs)[:30]))
    return "\n".join(out) or "No URLs/keys/pattern hits found."
