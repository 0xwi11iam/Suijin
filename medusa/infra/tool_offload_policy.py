"""Per-tool output-offload policy.

Declares for each registered tool whether output should be:
    "never"  — output is small/structured; always inline
    "always" — output is reliably huge; always offload to a file
    "auto"   — offload only if len(output) > OFFLOAD_THRESHOLD
"""
OFFLOAD_THRESHOLD = 50_000

OFFLOAD_POLICY = {
    "execute_terminal": "auto",
    "http_request": "never",  # HTML responses must be readable
    "search_kb": "never",
    "search_cve": "never",
    "write_note": "never",
    "check_knowledge": "never",
    "record_finding": "never",
    "read_file": "never",
    "write_file": "never",
    "nmap_scan": "auto",
    "gobuster_dir": "auto",
    "gobuster_dns": "auto",
    "ffuf_fuzz": "auto",
    "feroxbuster_scan": "auto",
    "nikto_scan": "auto",
    "sqlmap_scan": "auto",
    "hydra_brute": "auto",
    "amass_enum": "auto",
    "subfinder_enum": "auto",
    "nuclei_scan": "auto",
    "trufflehog_scan": "auto",
    "john_crack": "auto",
}


def get_offload_mode(tool_name: str) -> str:
    return OFFLOAD_POLICY.get(tool_name, "auto")
