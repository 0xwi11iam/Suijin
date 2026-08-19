import difflib
import json
import statistics
import time
from datetime import datetime, timezone


def json_tool(text: str = "", mode: str = "pretty") -> str:
    if not text:
        return "Error: text required"
    try:
        data = json.loads(text)
    except ValueError as e:
        return f"INVALID JSON: {e}"
    m = (mode or "pretty").lower()
    if m == "minify":
        return json.dumps(data, separators=(",", ":"))
    if m == "check":
        return f"VALID JSON ({len(text):,} chars)"
    return json.dumps(data, indent=2, sort_keys=False)


def text_diff(a: str = "", b: str = "") -> str:
    if a is None or b is None:
        return "Error: two texts required"
    d = list(difflib.unified_diff(a.splitlines(), b.splitlines(), fromfile="a", tofile="b", lineterm=""))
    return "\n".join(d) if d else "texts are identical"


def convert_base(value: str = "", from_base: int = 10, to_base: int = 16) -> str:
    v = (value or "").strip().lower().replace("0x", "")
    if not v:
        return "Error: value required"
    try:
        n = int(v, int(from_base or 10))
        tb = int(to_base or 16)
    except ValueError as e:
        return f"Error: {e}"
    if not 2 <= tb <= 36:
        return "Error: to_base must be 2-36"
    digs = "0123456789abcdefghijklmnopqrstuvwxyz"
    if tb == 10:
        return str(n)
    out = ""
    x = n
    while x:
        out = digs[x % tb] + out
        x //= tb
    prefix = {2: "0b", 8: "0o", 16: "0x"}.get(tb, f"base{tb}:")
    return f"{prefix}{out or '0'} ({n})"


def ts_convert(value: str = "") -> str:
    v = (value or "").strip()
    try:
        if not v:
            dt = datetime.now(timezone.utc)
        elif v.replace(".", "").isdigit():
            ts = float(v)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return f"{v} -> {dt.isoformat()}"
        else:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return f"{v} -> unix {dt.timestamp():.0f} ({time.strftime('%a %d %b %Y %H:%M:%S', dt.timetuple())} UTC)"
        return f"now: {dt.isoformat()} (unix {dt.timestamp():.0f})"
    except ValueError as e:
        return f"Error: {e}"


def basic_stats(numbers: str = "") -> str:
    if not numbers:
        return "Error: numbers required"
    try:
        nums = [float(x) for x in numbers.replace(",", " ").split()]
    except ValueError:
        return "Error: non-numeric values present"
    if not nums:
        return "Error: no numbers"
    return (
        f"n={len(nums)} mean={statistics.mean(nums):.4g} median={statistics.median(nums):.4g} "
        f"min={min(nums):.4g} max={max(nums):.4g} stdev={statistics.stdev(nums):.4g}"
        if len(nums) > 1
        else f"n=1 value={nums[0]}"
    )


def regex_test(pattern: str = "", text: str = "") -> str:
    import re as _re

    if not pattern or not text:
        return "Error: pattern and text required"
    try:
        rx = _re.compile(pattern)
    except _re.error as e:
        return f"Error: invalid regex: {e}"
    matches = list(rx.finditer(text))
    if not matches:
        return "No matches"
    out = [f"{len(matches)} match(es):"]
    for i, mth in enumerate(matches[:20], 1):
        groups = mth.groups()
        line = f"  {i}. {mth.group(0)!r} @ {mth.start()}"
        if groups:
            line += f" groups={groups!r}"
        out.append(line)
    return "\n".join(out)
