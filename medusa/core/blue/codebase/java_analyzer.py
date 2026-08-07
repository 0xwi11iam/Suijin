"""Java route extraction — Spring Boot."""
import re
from pathlib import Path

def extract_java_routes(root: Path) -> list:
    endpoints = []
    for jf in root.rglob("*.java"):
        if "test" in str(jf).lower(): continue
        try:
            source = jf.read_text(errors="ignore")
            for m in re.finditer(r'@(Get|Post|Put|Delete|Patch)Mapping\s*\(\s*(?:"([^"]+)"|value\s*=\s*"([^"]+)")', source):
                p = m.group(1) or m.group(2)
                endpoints.append({"method":re.search(r'@(\w+)Mapping',source[m.start()-30:m.start()]).group(1).upper(),"path":p,"file":str(jf),"line":source[:m.start()].count("\n")+1,"framework":"spring"})
        except: pass
    return endpoints
