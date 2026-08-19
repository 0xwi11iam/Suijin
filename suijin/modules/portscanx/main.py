import socket

_TOP = (21,22,23,25,53,80,81,88,110,111,135,139,143,389,443,445,465,587,593,623,636,993,995,1080,1099,1433,1521,2049,2181,2375,2376,3000,3128,3306,3389,4444,4848,5000,5432,5601,5672,5900,5984,6000,6379,6443,7001,7002,7077,8080,8081,8088,8180,8443,8500,8649,8888,9000,9001,9090,9092,9200,9300,9418,9999,10000,10250,11211,15672,27017,28017,32400,50000,50070,5555,5556,61616,8000,8008,8009,8089,8222,8280,8324,8530,8531,8883,9009,9100,9443,10162,11111,22222,27036,3310,3572,4443,554,5060,5061,6889,6970,7777,7990,8333,8686,9050,9350,9440,9500)

def tcp_scan(host: str = "", ports: str = "") -> str:
    if not host:
        return "Error: host required (authorized targets only)"
    h = host.strip()
    plist = [int(p) for p in ports.replace(",", " ").split() if p.isdigit()] if ports else list(_TOP)
    open_ports = []
    for p in plist[:200]:
        try:
            s = socket.create_connection((h, p), timeout=1.2)
            open_ports.append(p)
            s.close()
        except (socket.timeout, ConnectionRefusedError, OSError):
            continue
    if not open_ports:
        return f"No open ports in the {len(plist)}-port set (or filtered)."
    import re
    svc = {"22": "ssh", "80": "http", "443": "https", "3306": "mysql", "5432": "postgres", "6379": "redis", "27017": "mongodb", "9200": "elasticsearch", "2375": "docker-api!", "8500": "consul!", "11211": "memcached", "5900": "vnc", "445": "smb", "3389": "rdp"}
    lines = [f"  {p:>6}/tcp  {svc.get(str(p), '')}" for p in open_ports]
    return f"{len(open_ports)} open on {h}:\n" + "\n".join(lines) + "\n(pair with portmap/service_lookup for the follow-up play)"
