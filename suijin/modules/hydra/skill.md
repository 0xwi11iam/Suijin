# Hydra

> [warn] **LONG-RUNNING** — Always use `"background": true`. Brute-force attacks take minutes to hours.
> `{"tool_name": "hydra_brute", "tool_args": {"target": "...", "service": "...", "options": "...", "background": true}}` (`hydra_brute`)

Online password brute-forcing for SSH, HTTP, FTP, and many other services.

## Installation

| Method | Command |
|--------|---------|
| **Homebrew (macOS)** | `brew install hydra` |
| **APT (Kali/Debian)** | `sudo apt install hydra` |
| **Source** | `git clone https://github.com/vanhauser-thc/thc-hydra.git && cd thc-hydra && ./configure && make && sudo make install` |
| **Docker** | `docker run --rm -it vanhauser/hydra -l admin -P wordlist.txt TARGET ssh` |

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `target` | Yes | Target IP or hostname |
| `service` | Yes | Service type (see supported list below) |
| `options` | No | Extra hydra options (`-l USER -P WORDLIST`, etc.) |

## Supported Services (~50 protocols)

| Service Name | Module Flag | Default Port |
|-------------|-------------|-------------|
| SSH | `ssh` | 22 |
| FTP | `ftp` | 21 |
| HTTP POST form | `http-post-form` | 80/443 |
| HTTP GET auth | `http-get` | 80/443 |
| HTTPS POST form | `https-post-form` | 443 |
| SMB | `smb` | 445 |
| MySQL | `mysql` | 3306 |
| PostgreSQL | `postgres` | 5432 |
| MSSQL | `mssql` | 1433 |
| RDP | `rdp` | 3389 |
| VNC | `vnc` | 5900 |
| Telnet | `telnet` | 23 |
| POP3 | `pop3` | 110 |
| IMAP | `imap` | 143 |
| SMTP | `smtp` | 25 |
| LDAP | `ldap2` / `ldap3` | 389/636 |
| SNMP | `snmp` | 161 |
| Redis | `redis` | 6379 |
| MongoDB | `mongodb` | 27017 |
| Oracle | `oracle-listener` | 1521 |

## Essential Flags

| Flag | Description | Example |
|------|-------------|---------|
| `-l USER` | Single username | `-l admin` |
| `-L user.txt` | Username list | `-L /usr/share/wordlists/usernames.txt` |
| `-p PASS` | Single password | `-p password123` |
| `-P wordlist.txt` | Password list | `-P /usr/share/wordlists/rockyou.txt` |
| `-C combo.txt` | user:pass combo file | `-C /usr/share/seclists/Passwords/Default-Credentials/` |
| `-t 4` | Concurrent tasks | `-t 4` (max for SSH, 16 for HTTP) |
| `-f` | Stop after first find | `-f` |
| `-F` | Stop after per host first find | `-F` |
| `-v` | Verbose | `-v` |
| `-V` | Show each attempt | `-V` |
| `-e nsr` | Try null, same-as-user, reverse | `-e nsr` |
| `-s PORT` | Custom port | `-s 2222` |
| `-w 30` | Wait between retries (seconds) | `-w 30` (after 429 rate limit) |
| `-W 5` | Time between connections | `-W 5` |
| `-o output.txt` | Save results | `-o hydra_results.txt` |
| `-I` | Ignore restore file | `-I` |
| `-M targets.txt` | Multiple targets | `-M target_list.txt` |

## HTTP Form Bruteforce Syntax

The most complex but most useful: `http-post-form` requires the form string:

```
"/path:user=^USER^&pass=^PASS^:FAIL_STRING"
```

Parts separated by `:`:
1. Path: `/login`
2. POST body: `user=^USER^&pass=^PASS^` (^USER^ and ^PASS^ are placeholders)
3. Fail condition: `"Invalid credentials"` or `"Login failed"` — a string that appears on failure

## Workflows

### SSH brute-force
```json
{"tool": "hydra_brute", "args": {"target": "10.0.0.1", "service": "ssh", "options": "-l root -P /usr/share/wordlists/rockyou.txt -t 4 -f -I"}}
```

### FTP brute-force
```json
{"tool": "hydra_brute", "args": {"target": "10.0.0.1", "service": "ftp", "options": "-L /usr/share/wordlists/usernames.txt -P /usr/share/wordlists/rockyou.txt -t 8 -f -I"}}
```

### HTTP POST form login
```json
{"tool": "hydra_brute", "args": {"target": "10.0.0.1", "service": "http-post-form", "options": "-l admin -P /usr/share/wordlists/rockyou.txt -t 8 -f -I "/login:user=^USER^&pass=^PASS^:Invalid credentials""}}
```

### WordPress login
```json
{"tool": "execute_terminal", "args": {"cmd": "hydra -l admin -P /usr/share/wordlists/rockyou.txt 10.0.0.1 http-post-form -t 4 -I "/wp-login.php:log=^USER^&pwd=^PASS^&wp-submit=Log+In:is incorrect""}}
```

### MySQL brute-force
```json
{"tool": "hydra_brute", "args": {"target": "10.0.0.1", "service": "mysql", "options": "-l root -P /usr/share/wordlists/rockyou.txt -t 4 -f -I"}}
```

### Custom port (SSH on 2222)
```json
{"tool": "hydra_brute", "args": {"target": "10.0.0.1", "service": "ssh", "options": "-l admin -P /usr/share/wordlists/rockyou.txt -s 2222 -t 4 -f -I"}}
```

## Rate Limit Handling

If getting blocked (403/429), add delay:
```json
{"tool": "execute_terminal", "args": {"cmd": "hydra -l admin -P /usr/share/wordlists/rockyou.txt 10.0.0.1 ssh -t 1 -w 30 -f -I"}}
```
