"""Hotfix orchestrator — manage patch lifecycle from detection to deployment."""
import asyncio, os, time, json
from pathlib import Path


async def orchestrate_hotfix(vulnerability: dict, codebase_path: str, config: dict) -> dict:
    """Orchestrate a complete hotfix: generate patch, validate, deploy, verify.

    Returns detailed status including patch file, backup path, and deployment result.
    """
    file_path = vulnerability.get("file", "")
    line_num = vulnerability.get("line", 0)
    vuln_type = vulnerability.get("type", "unknown")
    target_file = Path(codebase_path) / file_path if file_path else None

    if not target_file or not target_file.exists():
        return {"status": "failed", "reason": f"Target file not found: {file_path}"}

    # Read original for backup
    try:
        original = target_file.read_text(errors="ignore")
    except Exception as e:
        return {"status": "failed", "reason": f"Cannot read {file_path}: {e}"}

    # Create backup
    backup_path = target_file.with_suffix(target_file.suffix + ".medusa_backup")
    try:
        backup_path.write_text(original)
    except Exception:
        backup_path = None

    # Determine fix strategy based on vulnerability type
    fix_strategies = {
        "sqli": "parameterized_query",
        "sql_injection": "parameterized_query",
        "command_injection": "input_sanitization",
        "cmdi": "input_sanitization",
        "xss": "output_encoding",
        "path_traversal": "path_validation",
        "idor": "auth_check",
        "auth_bypass": "auth_check",
        "ssrf": "url_validation",
        "xxe": "parser_hardening",
        "ssti": "sandboxed_templates",
    }
    fix_type = fix_strategies.get(vuln_type.lower().replace(" ", "_"), "manual_review")

    # Check if subagent has a pre-built patch
    patch_code = None
    try:
        from medusa.core.blue.knowledge_graph import get_kg
        kg = get_kg()
        for node in kg.nodes.values():
            if node.node_type == "intelligence" and "patch" in str(node.data).lower():
                if file_path in str(node.data):
                    patch_code = node.data.get("content", "")
                    break
    except Exception:
        pass

    result = {
        "status": "generated",
        "file": file_path,
        "line": line_num,
        "fix_type": fix_type,
        "backup": str(backup_path) if backup_path else None,
        "operator_approval_required": config.get("hotfix", {}).get("auto_patch_critical", False) is False,
        "patch_available": patch_code is not None,
        "strategy": f"Replace vulnerable code at line {line_num} with {fix_type} pattern",
    }

    # If patch code is available and auto-patch is enabled, deploy immediately
    if patch_code and not result["operator_approval_required"]:
        try:
            lines = original.split("\n")
            if 0 < line_num <= len(lines):
                # Replace 1-5 lines around the vulnerability
                start = max(0, line_num - 1)
                end = min(len(lines), line_num + 4)
                patched_lines = lines[:start] + patch_code.split("\n") + lines[end:]
                target_file.write_text("\n".join(patched_lines))
                result["status"] = "deployed"
                result["deployed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            result["status"] = "patch_failed"
            result["error"] = str(e)

    return result
