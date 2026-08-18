"""Gobuster wrapper."""
import shlex

from suijin.tools.result import run_command
def gobuster_dir(url, wordlist, extensions="", threads=20):
    if not url or not wordlist: return "Error: url and wordlist required"
    ext_flag = f"-x {extensions}" if extensions else ""
    cmd = f"gobuster dir -u {shlex.quote(url)} -w {shlex.quote(wordlist)} {ext_flag} -t {threads} --no-error --no-color"
    return run_command(cmd, shell=True, timeout=300, cwd="/tmp", command_text=cmd).format()
def gobuster_dns(domain, wordlist):
    if not domain or not wordlist: return "Error: domain and wordlist required"
    cmd = f"gobuster dns -d {shlex.quote(domain)} -w {shlex.quote(wordlist)} --no-color"
    return run_command(cmd, shell=True, timeout=300, cwd="/tmp", command_text=cmd).format()
