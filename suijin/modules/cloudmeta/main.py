import requests

_IMDS = [
    ("aws-v1", "http://{h}:80/latest/meta-data/", 200, True),
    ("aws-v2", "http://{h}:80/latest/api/token", 403, False),
    ("gcp", "http://{h}:8080/computeMetadata/v1/", 403, False),
    ("gcp-legacy", "http://{h}:8080/metadata/v1/", 200, True),
    ("azure", "http://{h}:80/metadata/instance?api-version=2021-02-01", 400, False),
    ("alibaba", "http://{h}:80/latest/meta-data/", 200, True),
    ("digitalocean", "http://{h}:80/metadata/v1.json", 200, True),
    ("k8s", "http://{h}:10256/healthz", 200, True),
    ("docker", "http://{h}:2375/version", 200, True),
    ("etcd", "http://{h}:2379/version", 200, True),
    ("consul", "http://{h}:8500/v1/status/leader", 200, True),
    ("redis", "http://{h}:6379/", 200, False),
]


def cloud_metadata_probe(host: str = "", port: int = 0) -> str:
    if not host:
        return "Error: host required (or host:port of the SSRF target's internal host)"
    h = host.strip().rstrip("/")
    hits = []
    ports = [int(port)] if port else [80, 8080, 8500, 2375, 2379, 6379, 10256]
    try:
        for name, tpl, want, _ in _IMDS:
            try:
                url = tpl.format(h=h if ":" in h else h)
                # only probe ports implied by the template's port when port unset
                import re as _re
                m = _re.search(r":(\d+)/", tpl)
                p = int(m.group(1)) if m else 80
                if port:
                    url = tpl.format(h=h).replace(f":{p}/", f":{int(port)}/", 1) if p != int(port) else tpl.format(h=h)
                elif p not in ports:
                    continue
                r = requests.get(url, timeout=(2, 6), headers={"User-Agent": "suijin-imds-probe"})
                if r.status_code == want:
                    hits.append(f"EXPOSED {name} -> {url} ({r.status_code}, {len(r.content)}B)")
                elif r.status_code not in (404, 405, 502, 503, -1):
                    hits.append(f"interesting {name} -> {url} ({r.status_code})")
            except requests.RequestException:
                continue
    except ValueError as e:
        return f"Error: {e}"
    if not hits:
        return "No metadata endpoints responded on common ports (or filtered)."
    return "\n".join(hits[:15])
