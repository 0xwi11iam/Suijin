# Nmap (`nmap_scan`)

> [warn] **LONG-RUNNING** — Always use `"background": true` for full port scans (-p-), script scans, or UDP scans.
> Background usage: `{"tool_name": "nmap_scan", "tool_args": {"target": "X", "flags": "-sV -sC -p-", "background": true}}`
> Check results with `job_status` / `job_wait` / `job_output`.

The #1 port scanner. Use for EVERY engagement before anything else.

## Installation

| Method | Command |
|--------|---------|
| **Homebrew (macOS)** | `brew install nmap` |
| **APT (Debian/Ubuntu/Kali)** | `sudo apt install nmap` |
| **YUM/DNF (RHEL/Fedora)** | `sudo yum install nmap` or `sudo dnf install nmap` |
| **Source** | `git clone https://github.com/nmap/nmap.git && cd nmap && ./configure && make && sudo make install` |
| **Windows** | Download from https://nmap.org/download.html |
| **Docker** | `docker run --rm -it instrumentisto/nmap -sV TARGET` |

## All Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `target` | Yes | — | IP address, hostname, or CIDR range (e.g. `192.168.1.0/24`) |
| `flags` | No | `-sV -sC` | Any nmap flags |

## Essential Nmap Flags

### Scan Types
| Flag | Description | Example |
|------|-------------|---------|
| `-sS` | TCP SYN scan (stealth, default as root) | `nmap -sS TARGET` |
| `-sT` | TCP Connect scan (default without root) | `nmap -sT TARGET` |
| `-sU` | UDP scan (slow) | `nmap -sU TARGET` |
| `-sV` | Version detection on open ports | `nmap -sV TARGET` |
| `-sC` | Default NSE scripts | `nmap -sC TARGET` |
| `-sN` | TCP Null scan (firewall evasion) | `nmap -sN TARGET` |
| `-sF` | TCP FIN scan | `nmap -sF TARGET` |
| `-sX` | TCP Xmas scan | `nmap -sX TARGET` |
| `-sP` / `-sn` | Ping sweep only | `nmap -sn 192.168.1.0/24` |
| `-sA` | ACK scan (firewall rule detection) | `nmap -sA TARGET` |
| `-sW` | Window scan | `nmap -sW TARGET` |
| `-sR` | RPC scan | `nmap -sR TARGET` |

### Port Specification
| Flag | Description | Example |
|------|-------------|---------|
| `-p 80,443` | Scan specific ports | `nmap -p 80,443 TARGET` |
| `-p-` | Scan all 65535 ports | `nmap -p- TARGET` |
| `-p 1-1000` | Port range | `nmap -p 1-1000 TARGET` |
| `--top-ports 100` | Most common 100 ports | `nmap --top-ports 100 TARGET` |
| `-F` | Fast mode (top 100 ports) | `nmap -F TARGET` |

### Output Formats
| Flag | Description |
|------|-------------|
| `-oN file.txt` | Normal output |
| `-oX file.xml` | XML output (for tools) |
| `-oG file.grepable` | Grepable output |
| `-oA basename` | All formats at once |
| `-v` | Verbose (`-vv` for very verbose) |

### Timing & Performance
| Flag | Description | Speed |
|------|-------------|-------|
| `-T0` | Paranoid (very slow, evades IDS) | Slowest |
| `-T1` | Sneaky | Very slow |
| `-T2` | Polite | Slow |
| `-T3` | Normal (default) | Normal |
| `-T4` | Aggressive | Fast |
| `-T5` | Insane (may miss ports) | Fastest |

### NSE Scripts
| Flag | Description |
|------|-------------|
| `-sC` | All default scripts |
| `--script=http-title` | Specific script |
| `--script=http-enum` | HTTP enumeration |
| `--script=vuln` | Vuln detection scripts |
| `--script=smb-enum-shares` | SMB share enumeration |

### Evasion
| Flag | Description |
|------|-------------|
| `-D RND:10` | Decoy scan (10 random IPs) |
| `--source-port 53` | Spoof source port (DNS bypass) |
| `--data-length 200` | Append random data to packets |
| `--ttl 128` | Set TTL value |
| `--spoof-mac Apple` | Spoof MAC address |
| `-f` | Fragment packets |
| `--mtu 24` | Set MTU for fragmentation |
| `--scan-delay 1s` | Add delay between probes |

## Workflows

### Standard full scan
```json
{"tool": "nmap_scan", "args": {"target": "10.0.0.1", "flags": "-sV -sC -p- -T4"}}
```

### Quick check
```json
{"tool": "nmap_scan", "args": {"target": "10.0.0.1", "flags": "-sn"}}
```

### Vuln scanning
```json
{"tool": "nmap_scan", "args": {"target": "10.0.0.1", "flags": "--script=vuln"}}
```

### Stealth scan with decoys
```json
{"tool": "nmap_scan", "args": {"target": "10.0.0.1", "flags": "-sS -sV -D RND:10 -T2"}}
```

### UDP scan (services like DNS, SNMP)
```json
{"tool": "nmap_scan", "args": {"target": "10.0.0.1", "flags": "-sU --top-ports 20"}}
```

## Response Interpretation

| Port State | Meaning |
|-----------|---------|
| `open` | Service is actively accepting connections |
| `filtered` | Firewall is blocking the probe |
| `closed` | Port is accessible but no service listening |
| `unfiltered` | Port is accessible but state unknown |
