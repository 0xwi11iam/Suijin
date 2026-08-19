import re

_OFFLINE = {
    "00:50:56": "VMware",
    "00:0c:29": "VMware",
    "00:05:69": "VMware",
    "00:1c:14": "VMware",
    "52:54:00": "QEMU/KVM",
    "0a:00:27": "VirtualBox (Intel)",
    "08:00:27": "VirtualBox",
    "00:15:5d": "Hyper-V",
    "00:03:ff": "Microsoft Virtual",
    "3c:ec:ef": "Amazon",
    "06:b5:1e": "AWS (modern)",
    "0a:d4:64": "AWS",
    "42:01:0a": "GCP",
    "42:66:7f": "GCP (older)",
    "00:1d:d8": "Microsoft Azure",
    "0c:db:5e": "Azure",
    "d0:0d:1e": "Docker (container!)",
    "b8:27:eb": "Raspberry Pi",
    "dc:a6:32": "Raspberry Pi 4",
    "00:1a:2b": "Ayecom (embedded)",
    "00:04:4b": "Nortel",
    "c8:69:cd": "Hikvision",
    "44:47:cc": "Hikvision",
    "80:19:34": "Ubiquiti",
    "24:a4:3c": "Ubiquiti",
    "fc:ec:da": "Ubiquiti",
    "00:1b:eb": "Cisco",
    "00:26:99": "Cisco",
    "f8:72:ea": "Cisco",
    "00:25:90": "Juniper",
    "28:c0:da": "Juniper",
    "00:0e:c6": "Hewlett-Packard",
    "3c:d9:2b": "HP",
    "b4:99:ba": "Huawei",
    "88:28:b3": "Huawei",
    "cc:a2:23": "Huawei",
    "00:23:cd": "Samsung",
    "8c:16:45": "Samsung",
    "ac:de:48": "private/random (Apple likely)",
}


def mac_vendor(mac: str = "") -> str:
    m = (mac or "").strip().lower().replace("-", ":").replace(".", ":")
    if not re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", m):
        return f"Error: '{mac}' is not a MAC address"
    prefix = ":".join(m.split(":")[:3])
    if prefix in _OFFLINE:
        hint = (
            "VIRTUALIZATION/CONTAINER"
            if any(
                x in _OFFLINE[prefix]
                for x in ("VMware", "QEMU", "VBox", "Hyper-V", "Virtual", "Docker", "AWS", "GCP", "Azure")
            )
            else "hardware"
        )
        return f"{m} -> {_OFFLINE[prefix]} ({hint})"
    # second-prefix try (larger orgs own blocks)
    for p, v in _OFFLINE.items():
        if p.startswith(prefix[:5]):
            return f"{m} -> {v} (near match on {p})"
    return f"{m} -> unknown to the offline table (cloud devices often use dynamic MACs; try macvendors.com/API)"


def _unused():  # keep requests import lazy-free
    return None
