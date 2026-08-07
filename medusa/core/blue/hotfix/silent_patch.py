"""Silent patch — fix vulnerability but keep endpoint alive as trap."""
def silent_patch(vulnerability: dict, source_code: str) -> tuple:
    from medusa.core.blue.hotfix.patch_generator import generate_patch
    fixed_code = generate_patch(vulnerability, source_code)
    trap_code = source_code
    return fixed_code, trap_code
