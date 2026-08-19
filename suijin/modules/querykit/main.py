import csv
import io
import json


def json_query(json_text: str = "", path: str = "") -> str:
    if not json_text or not path:
        return "Error: json and path required"
    try:
        data = json.loads(json_text)
    except ValueError as e:
        return f"Error: invalid JSON: {e}"
    cur = data
    for part in path.strip(".").split("."):
        if not part:
            continue
        if part.endswith("[*]"):
            key = part[:-3]
            if not isinstance(cur, dict) or key not in cur or not isinstance(cur[key], list):
                return f"Error: {key}[*] not a list at this path"
            cur = cur[key]
        elif isinstance(cur, dict):
            if part not in cur:
                return f"Error: key {part!r} not found (keys: {sorted(cur)[:10]})"
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit():
            i = int(part)
            if i >= len(cur):
                return f"Error: index {i} out of range ({len(cur)})"
            cur = cur[i]
        elif isinstance(cur, list):
            # key after [*]: map extraction across the list
            mapped = [item[part] for item in cur if isinstance(item, dict) and part in item]
            if not mapped and cur:
                return f"Error: key {part!r} not found in any of {len(cur)} items"
            cur = mapped
        else:
            return f"Error: cannot descend {part!r} into {type(cur).__name__}"
    return json.dumps(cur, indent=2, default=str)[:4000]


def csv_query(csv_text: str = "", where: str = "", sort: str = "") -> str:
    if not csv_text.strip():
        return "Error: csv required"
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    if not rows:
        return "No rows"
    if where and "=" in where:
        col, _, val = where.partition("=")
        col, val = col.strip(), val.strip()
        if col not in rows[0]:
            return f"Error: column {col!r} not in {list(rows[0])}"
        rows = [r for r in rows if r.get(col, "").strip() == val]
    if sort:
        sort = sort.strip()
        if sort not in rows[0]:
            return f"Error: column {sort!r} not in {list(rows[0])}"
        rows.sort(key=lambda r: r.get(sort, ""))
    if not rows:
        return f"0 rows match {where!r}"
    out = ["\t".join(rows[0].keys())] + ["\t".join(r.values()) for r in rows[:50]]
    return f"{len(rows)} rows:\n" + "\n".join(out)


def table_markdown(json_text: str = "", columns: str = "") -> str:
    if not json_text.strip():
        return "Error: json array required"
    try:
        rows = json.loads(json_text)
    except ValueError as e:
        return f"Error: invalid JSON: {e}"
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return "Error: expected a JSON array of objects"
    cols = [c.strip() for c in columns.split(",") if c.strip()] or list(rows[0].keys())[:8]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows[:50]:
        lines.append("| " + " | ".join(str(r.get(c, ""))[:40] for c in cols) + " |")
    return "\n".join(lines)
