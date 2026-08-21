import time
import urllib.parse

import requests


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:  # standalone fallback
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


_UA = {"User-Agent": _stealth_ua()}


def cors_check(url: str = "") -> str:
    if not url:
        return "Error: url required"
    origin = "https://evil.example"
    try:
        r = requests.get(url, headers={**_UA, "Origin": origin}, timeout=(5, 15))
        acao = r.headers.get("access-control-allow-origin")
        acac = r.headers.get("access-control-allow-credentials")
        if not acao:
            return "No CORS headers reflected — policy likely absent/closed"
        refl = acao == origin
        star = acao == "*"
        verdict = "REFLECTS arbitrary Origin" if refl else ("wildcard *" if star else "fixed origin")
        if (refl or star) and acac == "true":
            verdict += " + CREDENTIALS — report-grade CORS misconfig"
        return f"ACAO: {acao} | ACAC: {acac}\n{verdict}"
    except requests.RequestException as e:
        return f"Error: {e}"


def vhost_check(url: str = "", vhost: str = "") -> str:
    if not url or not vhost:
        return "Error: url and vhost required"
    try:
        p = urllib.parse.urlparse(url if "://" in url else "http://" + url)
        base = requests.get(url, headers={**_UA, "Host": p.netloc}, timeout=(5, 15))
        cand = requests.get(url, headers={**_UA, "Host": vhost}, timeout=(5, 15))
        if cand.status_code == 404 and base.status_code == 200:
            return f"vhost {vhost}: NOT routed (404 on default pool)"
        if abs(len(cand.content) - len(base.content)) > 256 or cand.status_code != base.status_code:
            return (
                f"vhost {vhost}: DIFFERENT response — routed separately! "
                f"base {base.status_code}/{len(base.content):,}B vs vhost {cand.status_code}/{len(cand.content):,}B — enumerate its stack independently"
            )
        return f"vhost {vhost}: same pool as default ({cand.status_code}, size delta {abs(len(cand.content) - len(base.content))}B)"
    except requests.RequestException as e:
        return f"Error: {e}"


def timing_stats(url: str = "", n: int = 8) -> str:
    if not url:
        return "Error: url required"
    try:
        n = max(2, min(int(n or 8), 20))
        times = []
        for _ in range(n):
            t0 = time.perf_counter()
            requests.get(url, headers=_UA, timeout=(5, 20))
            times.append((time.perf_counter() - t0) * 1000)
        mean = sum(times) / len(times)
        srt = sorted(times)
        p95 = srt[int(0.95 * (len(srt) - 1))]
        spread = (max(times) - min(times)) / max(mean, 0.001)
        note = ""
        if spread > 0.8:
            note = "HIGH variance — filtering by content/state: timing side channel candidate"
        elif spread < 0.15:
            note = "very uniform — no obvious timing difference"
        return f"{n} GETs: mean {mean:.0f}ms p95 {p95:.0f}ms min {min(times):.0f} max {max(times):.0f} (spread {spread:.2f}) {note}"
    except requests.RequestException as e:
        return f"Error: {e}"
