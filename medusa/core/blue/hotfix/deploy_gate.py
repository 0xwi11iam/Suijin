"""Deploy gate — operator approval for production deployment."""
from __future__ import annotations
def request_approval(patch_summary: str, test_results: dict) -> bool:
    print(f"\nPATCH APPROVAL REQUIRED:\n{patch_summary}\nTests: {test_results}\nDeploy? [Y/n] ", end="")
    try:
        return input().strip().lower() != "n"
    except Exception:
        import logging; logging.getLogger("medusa").warning("Deploy gate check failed", exc_info=True)
        return False

def auto_deploy_if_safe(patch: dict, config: dict) -> bool:
    if config.get("hotfix",{}).get("auto_patch_critical", False):
        return patch.get("severity") == "critical"
    return False
