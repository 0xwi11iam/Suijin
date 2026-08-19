_PORTS = {
    21: ("ftp", "anonymous-login checks, banner grab"),
    22: ("ssh", "version, user enumeration via auth errors, key brute"),
    23: ("telnet", "cleartext — creds, banner"),
    25: ("smtp", "open relay, VRFY user enum"),
    53: ("dns", "zone transfer AXFR, subdomain brute"),
    69: ("tftp", "config file fetch, no auth"),
    80: ("http", "full web methodology"),
    88: ("kerberos", "user enumeration (AS-REP roasting)"),
    110: ("pop3", "cleartext mail"),
    111: ("rpcbind", "rpcinfo, NFS export discovery"),
    123: ("ntp", "monlist amplification"),
    135: ("msrpc", "Windows enum"),
    139: ("netbios-ssn", "null session enum"),
    143: ("imap", "cleartext mail"),
    161: ("snmp", "public/community string walk — juicy configs"),
    389: ("ldap", "anonymous bind, dir dump"),
    443: ("https", "web + TLS audit"),
    445: ("smb", "null sessions, shares, EternalBlue, relay"),
    465: ("smtps", "mail"),
    500: ("ike/ipsec", " aggressive mode PSK grab"),
    502: ("modbus", "unauth industrial control"),
    512: ("rexec", "legacy r-services"),
    513: ("rlogin", "legacy r-services"),
    514: ("syslog", "write abuse"),
    587: ("submission", "mail auth"),
    623: ("ipmi", "cipher-0 auth bypass"),
    636: ("ldaps", "dir dump"),
    993: ("imaps", "mail"),
    995: ("pop3s", "mail"),
    1080: ("socks", "open proxy check"),
    1433: ("mssql", "sa brute, xp_cmdshell"),
    1521: ("oracle", "TNS version, default accts"),
    2049: ("nfs", "showmount -e, mount world-readable exports"),
    2181: ("zookeeper", "unauth 4lw cmds"),
    2375: ("docker", "UNAUTH docker API — container escape gold"),
    2376: ("docker-tls", "docker API"),
    3000: ("node/dev", "grafana/node/express dev panels"),
    3306: ("mysql", "root/weak creds, UDF rce"),
    3389: ("rdp", "BlueKeep-era checks, NLA status"),
    4444: ("metasploit", "default handler port — artifact of prior test"),
    4848: ("glassfish", "admin console"),
    5432: ("postgresql", "postgres/weak creds, COPY PROGRAM rce"),
    5601: ("kibana", "unauth dashboards"),
    5900: ("vnc", "unauth access, weak perms"),
    5984: ("couchdb", "unauth admin (older)"),
    6379: ("redis", "unauth INFO, CONFIG SET dir webshell"),
    6443: ("kubernetes api", "unauth service account tokens"),
    7001: ("weblogic", "console + known deserials"),
    8000: ("http-alt", "dev servers"),
    8080: ("http-proxy", "tomcat/jenkins/proxy managers"),
    8081: ("http-alt", "jenkins/supervisord"),
    8443: ("https-alt", "tomcat ssl, vCenter"),
    8888: ("http-alt", "jupyter (unauth kernel = RCE)"),
    9000: ("sonarqube/php-fpm", "app servers"),
    9001: ("tor/supervisord", "ctl"),
    9090: ("websm/prometheus", "metrics leak creds"),
    9200: ("elasticsearch", "unauth _cat/indices, data dump"),
    11211: ("memcached", "unauth key dump (get slab/keys)"),
    27017: ("mongodb", "unauth dbs"),
    50000: ("sap", "dispatcher"),
}


def service_lookup(query: str = "") -> str:
    q = (query or "").strip().lower()
    if not q:
        return "Error: query required (port number or service name)"
    if q.isdigit():
        port = int(q)
        hit = _PORTS.get(port)
        if hit:
            return f"{port}/tcp: {hit[0]} — {hit[1]}"
        well_known = " (well-known range)" if port < 1024 else ""
        return f"{port}: no common binding in the offline KB{well_known}; banner-grab it: nc/execute_terminal or ssl probe if TLS"
    hits = [(p, s) for p, s in _PORTS.items() if q in s[0]]
    if not hits:
        return f"service {q!r} not in the offline KB"
    return f"ports for {q!r}:\n" + "\n".join(f"  {p}: {s[0]} — {s[1]}" for p, s in sorted(hits))
