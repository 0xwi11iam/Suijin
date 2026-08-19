# Amass

> ⚠️ **LONG-RUNNING** — Use `"background": true` for active enumeration (-active flag). Passive mode is fast.
> `{"tool_name": "amass_enum", "tool_args": {"domain": "...", "flags": "-active", "background": true}}` (`amass_enum`)

Subdomain enumeration via OSINT + DNS.

## Installation

- Homebrew: `brew install amass`
- APT: `sudo apt install amass`
- Go: `go install -v github.com/owasp-amass/amass/v4/...@master`

## Usage

```json
{"tool": "amass_enum", "args": {"domain": "example.com", "flags": "-passive"}}
```

## Flags

| Flag | Speed | Coverage |
|------|-------|----------|
| `-passive` | Fastest | OSINT only |
| `-active` | Medium | OSINT + DNS |
| `-active -brute` | Slow | Full brute-force |

Add `-ip` to show resolved IPs.

## Data Sources (passive includes)

AlienVault, AnubisDB, BinaryEdge, Censys, CertSpotter, Crtsh, GitHub, HackerTarget, Netcraft, PassiveTotal, RapidDNS, SecurityTrails, Shodan, Sublist3r, ThreatCrowd, VirusTotal, Yahoo, and more.

## Workflows

```json
{"tool": "amass_enum", "args": {"domain": "target.com", "flags": "-passive -o /tmp/subdomains.txt"}}
```
Then verify with gobuster:
```json
{"tool": "gobuster_dns", "args": {"domain": "target.com", "wordlist": "/tmp/subdomains.txt"}}
```
