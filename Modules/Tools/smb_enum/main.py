"""SMB Enumeration — list shares, null sessions, anonymous access."""
import subprocess, os

def smb_list_shares(target, username="", password=""):
    """List SMB shares on a target. Supports null/anonymous sessions."""
    cmd = ["smbclient", "-L", f"//{target}", "-N"]
    if username:
        cmd = ["smbclient", "-L", f"//{target}", "-U", f"{username}%{password}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.stdout[:3000] if r.stdout else f"Error: {r.stderr[:500]}"
    except FileNotFoundError: return "smbclient not installed. Run: brew install samba"
    except Exception as e: return f"Error: {e}"

def smb_check_null_session(target):
    """Check if null session (anonymous access) is allowed."""
    return smb_list_shares(target)
