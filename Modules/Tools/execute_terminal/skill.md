# Shell Execution (`execute_terminal`)

Run **any** shell command. This is the primary interface for all CLI tools on the system.

## Installation

The tool is built-in (uses `subprocess`). CLI tools you install become available here.

| Install Method | Command |
|---------------|---------|
| **Homebrew** | `brew install <tool>` |
| **APT (Linux)** | `sudo apt install <tool>` |
| **PIP** | `pip3 install <tool>` |
| **Go install** | `go install github.com/.../@latest` |
| **Git clone** | `git clone ... && cd ... && make install` |

## Supported Tools via execute_terminal

| Category | Tools |
|----------|-------|
| Web recon | `curl`, `wget`, `gobuster`, `feroxbuster`, `ffuf`, `nikto`, `dirb`, `whatweb`, `wpscan` |
| Port scanning | `nmap`, `masscan`, `rustscan`, `unicornscan` |
| Subdomain enum | `amass`, `sublist3r`, `subfinder`, `assetfinder`, `dnsrecon` |
| Vuln scanning | `sqlmap`, `nikto`, `nuclei`, `wapiti`, `skipfish` |
| Brute force | `hydra`, `medusa`, `ncrack`, `crowbar`, `patator` |
| Hash cracking | `john`, `hashcat`, `hashid`, `hash-identifier` |
| Exploitation | `metasploit`, `searchsploit`, `empire`, `crackmapexec` |
| Network | `socat`, `netcat`, `ncat`, `socat`, `sslscan`, `testssl.sh` |
| Crypto/SSL | `openssl`, `sslscan`, `testssl.sh`, `certigo`, `cipherscan` |
| Post-exploit | `enum4linux`, `ldapsearch`, `impacket`, `bloodhound`, `powerview` |
| Wireless | `airodump-ng`, `aireplay-ng`, `aircrack-ng`, `reaver`, `wash` |
| Misc | `jq`, `yq`, `xxd`, `base64`, `grep`, `awk`, `sed` |

## All Flags and Options

The tool passes commands directly to the shell. Common patterns:

| Pattern | Example |
|---------|---------|
| Simple command | `ls -la /tmp` |
| Chained commands | `nmap -sV TARGET && gobuster dir -u http://TARGET -w wordlist.txt` |
| Pipelines | `cat file.txt \| grep "pattern" \| jq .` |
| Background | `python3 server.py &` |
| With timeout | `{"tool": "execute_terminal", "args": {"cmd": "nmap -sV TARGET", "timeout": 120}}` |

## Workflows

### Standard recon chain
```json
{"tool": "execute_terminal", "args": {"cmd": "nmap -sV -sC -p- TARGET", "timeout": 180}}
{"tool": "execute_terminal", "args": {"cmd": "gobuster dir -u http://TARGET -w /usr/share/wordlists/dirb/common.txt -t 50"}}
```
### Web app scanning
```json
{"tool": "execute_terminal", "args": {"cmd": "nikto -h http://TARGET -no404", "timeout": 120}}
```
### Password brute-forcing
```json
{"tool": "execute_terminal", "args": {"cmd": "hydra -l admin -P /usr/share/wordlists/rockyou.txt TARGET ssh", "timeout": 120}}
```

## Security Notes

- Commands like `pip install`, `sudo`, `brew install`, `apt install` require user approval
- The command runs with `cwd = medusa_agent/` by default
- Output is truncated at 8000 characters
- PID self-preservation: refuses to kill its own process
