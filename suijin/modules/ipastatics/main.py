import plistlib
import re
import zipfile
from pathlib import Path


def ipa_info(ipa_path: str = "") -> str:
    p = Path(ipa_path).expanduser()
    if not p.is_file():
        return f"Error: {p} not found"
    try:
        z = zipfile.ZipFile(p)
    except zipfile.BadZipFile:
        return "Error: not a valid zip/ipa"
    apps = [n for n in z.namelist() if n.endswith(".app/Info.plist")]
    prov = [n for n in z.namelist() if n.endswith("embedded.mobileprovision")]
    out = []
    for a in apps[:3]:
        try:
            info = plistlib.loads(z.read(a))
            out.append(f"{a}:")
            for k in ("CFBundleIdentifier", "CFBundleDisplayName", "CFBundleVersion", "CFBundleShortVersionString", "ITSAppUsesNonExemptEncryption", "NSAppTransportSecurity"):
                if k in info:
                    out.append(f"  {k} = {info[k]}")
            ats = info.get("NSAppTransportSecurity") or {}
            if ats.get("NSAllowsArbitraryLoads"):
                out.append("  !! ATS allows arbitrary loads (HTTP permitted)")
        except Exception as e:
            out.append(f"{a}: plist parse failed ({e})")
    for pr in prov[:1]:
        blob = z.read(pr)
        m = re.findall(rb"<key>(Name|TeamName|CreationDate|ExpirationDate|application-identifier)</key>\s*<(string|date)>([^<]+)<", blob)
        out.append("provisioning:")
        for k, _t, v in m[:8]:
            out.append(f"  {k.decode()} = {v.decode(errors='replace')[:60]}")
    return "\n".join(out) if out else "No .app/Info.plist found inside."
