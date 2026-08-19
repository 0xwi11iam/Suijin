import requests

_DEFAULT = "www,mail,remote,blog,webmail,server,ns1,ns2,smtp,secure,vpn,m,shop,ftp,mail2,test,portal,ns,ww1,host,support,beta,admin,store,dev,api,git,ci,internal,intranet,staging,backup,s3,cdn,app,apps,dashboard,monitor,status,id,ldap,ad,wiki,docs,jenkins,grafana,kibana"

def dns_brute(domain: str = "", words: str = "") -> str:
    if not domain:
        return "Error: domain required"
    d = domain.strip().lower().strip(".")
    wl = [w.strip() for w in (words.replace(",", " ") if words else _DEFAULT).split() if w.strip()][:120]
    found = []
    sess = requests.Session()
    for w in wl:
        name = f"{w}.{d}"
        try:
            r = sess.get("https://1.1.1.1/dns-query", params={"name": name, "type": "A"},
                         headers={"Accept": "application/dns-json"}, timeout=(2, 6))
            ans = r.json().get("Answer") or []
            if ans:
                ips = [a.get("data") for a in ans if a.get("type") == 1][:3]
                found.append(f"{name} -> {', '.join(ips)}")
        except requests.RequestException:
            continue
    return (f"{len(found)}/{len(wl)} resolved\n  " + "\n  ".join(found)) if found else f"0/{len(wl)} resolved"
