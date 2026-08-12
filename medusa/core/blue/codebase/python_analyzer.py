"""Python route extraction — Flask, Django, FastAPI."""
import re
from pathlib import Path

def extract_python_routes(root: Path) -> list:
    endpoints = []
    for pyfile in root.rglob("*.py"):
        if any(s in str(pyfile) for s in ["__pycache__", ".venv", "venv", "test_", "node_modules"]):
            continue
        try:
            source = pyfile.read_text(errors="ignore")
            for m in re.finditer(r'@(?:\w+\.)?route\s*\(\s*["\']([^"\']+)["\']', source):
                ep = {"method":"GET","path":m.group(1),"file":str(pyfile),"line":source[:m.start()].count("\n")+1,"framework":"flask"}
                endpoints.append(ep)
            for m in re.finditer(r'@(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', source):
                endpoints.append({"method":m.group(1).upper(),"path":m.group(2),"file":str(pyfile),"line":source[:m.start()].count("\n")+1,"framework":"fastapi"})
            for m in re.finditer(r'path\s*\(\s*["\']([^"\']+)["\']\s*,\s*(\w+)', source):
                endpoints.append({"method":"ANY","path":m.group(1),"file":str(pyfile),"line":source[:m.start()].count("\n")+1,"framework":"django","view":m.group(2)})
        except Exception:
            continue
    return endpoints
