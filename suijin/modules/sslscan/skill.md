# SSLScan

> [warn] **LONG-RUNNING** — Use `"background": true` for full cipher enumeration.
> `{"tool_name": "sslscan_check", "tool_args": {"target": "...", "background": true}}` (`sslscan_check`)

SSL/TLS cipher assessment. Identifies weak ciphers, outdated protocols, cert issues.

## Installation

- Homebrew: `brew install sslscan`
- APT: `sudo apt install sslscan`
- Git: `git clone https://github.com/rbsec/sslscan.git && cd sslscan && make static`

## Usage

```json
{"tool": "sslscan_check", "args": {"target": "example.com:443"}}
```

## Findings Severity

| Severity | Example |
|----------|---------|
| CRITICAL | SSLv2, NULL ciphers, RC4, Heartbleed |
| HIGH | SSLv3, EXPORT ciphers, weak DH, expired cert |
| MEDIUM | TLSv1.0, CBC ciphers, no PFS |
| LOW | Missing HSTS, incomplete chain |

## Alternative: testssl.sh

```json
{"tool": "execute_terminal", "args": {"cmd": "testssl --logfile /tmp/ssl.txt https://example.com", "timeout": 120}}
```
