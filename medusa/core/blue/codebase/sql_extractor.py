"""SQL extractor — find all SQL queries, classify parameterized vs raw."""
from __future__ import annotations
import re
from pathlib import Path

def extract_sql_queries(root: Path) -> list:
    queries = []
    for pyfile in root.rglob("*.py"):
        try:
            source = pyfile.read_text(errors="ignore")
            for m in re.finditer(r'\.execute\s*\(\s*["\']([^"\']*(?:SELECT|INSERT|UPDATE|DELETE)[^"\']*)', source, re.IGNORECASE):
                sql = m.group(1)
                queries.append({"file":str(pyfile),"line":source[:m.start()].count("\n")+1,"sql":sql[:200],"parameterized":"%s" in sql or "?" in sql})
        except Exception:
            continue
    return queries
