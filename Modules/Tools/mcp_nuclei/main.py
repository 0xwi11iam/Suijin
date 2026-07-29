"""MCP Nuclei — JSON output with deduplication."""
import subprocess, shlex, json

def mcp_nuclei_scan(target, templates="cve,exposures,misconfig", flags="-jsonl -silent -stats"):
    if not target: return "Error: target required"
    cmd = f"nuclei -u {shlex.quote(target)} -t {shlex.quote(templates)} {flags}"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd="/tmp")
    if not r.stdout.strip(): return r.stderr or "(no findings)"
    # Parse JSONL, deduplicate by template-id
    seen = set()
    findings = []
    for line in r.stdout.strip().split("\n"):
        try:
            f = json.loads(line)
            tid = f.get("template-id","")
            if tid not in seen:
                seen.add(tid)
                findings.append({
                    "template": tid, "name": f.get("info",{}).get("name",""),
                    "severity": f.get("info",{}).get("severity",""),
                    "matched": f.get("matched-at",""), "curl": f.get("curl-command","")
                })
        except: pass
    return json.dumps(findings, indent=2) if findings else "(no parsed findings)"

def mcp_nuclei_update():
    r = subprocess.run("nuclei -update-templates -silent", shell=True, capture_output=True, text=True, timeout=120)
    return r.stdout or r.stderr or "Templates updated"