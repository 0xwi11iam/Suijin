import datetime as _dt


def _parse_field(field: str, lo: int, hi: int) -> set:
    vals = set()
    for part in field.split(","):
        if not part:
            continue
        step = 1
        if "/" in part:
            part, _, s = part.partition("/")
            step = int(s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, _, b = part.partition("-")
            start, end = int(a), int(b)
        else:
            start = end = int(part)
            if step != 1:
                end = hi
        for v in range(start, end + 1, step):
            if lo <= v <= hi:
                vals.add(v)
    return vals


def _fields(expr: str):
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("need exactly 5 fields: minute hour day month weekday")
    minute = _parse_field(parts[0], 0, 59)
    hour = _parse_field(parts[1], 0, 23)
    day = _parse_field(parts[2], 1, 31)
    month = _parse_field(parts[3], 1, 12)
    weekday = {d % 7 for d in _parse_field(parts[4], 0, 7)}  # 0/7=Sunday
    return minute, hour, day, month, weekday


def cron_next(expr: str = "", n: int = 3) -> str:
    if not expr:
        return "Error: expr required"
    try:
        minute, hour, day, month, weekday = _fields(expr.strip())
    except ValueError as e:
        return f"Error: {e}"
    want = max(1, min(int(n or 3), 10))
    now = _dt.datetime.now(_dt.timezone.utc).replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
    runs = []
    cur = now
    while len(runs) < want and cur < now + _dt.timedelta(days=366):
        if (
            cur.minute in minute
            and cur.hour in hour
            and cur.day in day
            and cur.month in month
            and cur.weekday() in weekday
        ):
            runs.append(cur.strftime("%Y-%m-%d %H:%M UTC (%a)"))
            cur += _dt.timedelta(minutes=1)
        else:
            cur += _dt.timedelta(minutes=1)
    if not runs:
        return "No runs in the next year (check the expression)"
    return "\n".join(f"  {r}" for r in runs)


def cron_explain(expr: str = "") -> str:
    if not expr:
        return "Error: expr required"
    try:
        m, h, _d, _mo, _wd = _fields(expr.strip())
    except ValueError as e:
        return f"Error: {e}"
    bits = []
    bits.append(
        "every minute"
        if len(m) == 60 and len(h) == 24
        else ("hourly at :%02d" % list(m)[0] if len(h) == 24 else f"at {sorted(h)}h:{sorted(m)}")
    )
    dom = expr.split()[2]
    if dom != "*":
        bits.append(f"day-of-month {dom}")
    dow = expr.split()[4]
    if dow != "*":
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        bits.append("on " + "/".join(names[d] for d in sorted(_parse_field(dow, 0, 7) - {7}) or [0]))
    return f"'{expr.strip()}' -> " + ", ".join(bits)
