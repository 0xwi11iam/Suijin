# John the Ripper

> ⚠️ **LONG-RUNNING** — Always use `"background": true`. Password cracking can take hours.
> `{"tool_name": "john_crack", "tool_args": {"hashfile": "...", "wordlist": "...", "background": true}}` (`john_crack`)

Offline hash cracker. Supports MD5, SHA, NTLM, bcrypt, descrypt, SSH keys, archives.

## Installation

- Homebrew: `brew install john-jumbo`
- APT: `sudo apt install john`

## Usage

```json
{"tool": "john_crack", "args": {"hashfile": "/tmp/hashes.txt", "wordlist": "/usr/share/wordlists/rockyou.txt"}}
{"tool": "john_crack", "args": {"hashfile": "/tmp/hashes.txt", "format": "nt"}}
```

## Common Formats

| Format | Hash Type |
|--------|-----------|
| `raw-md5` | MD5 |
| `raw-sha256` | SHA-256 |
| `nt` | NTLM (Windows) |
| `bcrypt` | bcrypt ($2b$/$2y$) |
| `sha512crypt` | SHA-512 ($6$) |
| `descrypt` | DES (old Unix) |
| `krb5tgs` | Kerberos TGS |
| `ssh-ng` | SSH private key |

## Extract + Crack Workflow

1. Extract: `ssh2john id_rsa > hash.txt` or `zip2john archive.zip > hash.txt`
2. Crack: `{"tool": "john_crack", "args": {"hashfile": "hash.txt", "wordlist": "/usr/share/wordlists/rockyou.txt"}}`
3. Show: `{"tool": "execute_terminal", "args": {"cmd": "john --show hash.txt"}}`

## Common Extraction Commands

| Source | Command |
|--------|---------|
| SSH key | `ssh2john id_rsa > hash.txt` |
| ZIP | `zip2john archive.zip > hash.txt` |
| RAR | `rar2john archive.rar > hash.txt` |
| Keepass | `keepass2john database.kdbx > hash.txt` |
| Linux shadow | `unshadow /etc/passwd /etc/shadow > hash.txt` |
