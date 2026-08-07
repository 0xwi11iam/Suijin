"""Breadcrumb layer — plant fake credentials and API keys as bait."""
import json, os, random, string
from pathlib import Path

def plant_breadcrumbs(target_dir: str) -> list:
    crumbs = []
    bait = Path(target_dir) / ".medusa_bait"
    bait.mkdir(exist_ok=True)
    fake_env = bait / ".env.bak"
    canary_key = "ak_canary_" + ''.join(random.choices(string.ascii_lowercase+string.digits, k=12))
    fake_env.write_text(f"AWS_ACCESS_KEY_ID=AKIA{canary_key.upper()}\nAWS_SECRET_ACCESS_KEY={canary_key}\nJWT_SECRET=canary_{canary_key}")
    crumbs.append({"type": "env_file", "path": str(fake_env), "canary": canary_key})
    fake_git = bait / ".git" / "config"
    fake_git.parent.mkdir(exist_ok=True)
    fake_git.write_text('[remote "origin"]\nurl = https://' + canary_key + ':x-oauth-basic@github.com/fake/private-repo.git')
    crumbs.append({"type": "git_config", "path": str(fake_git), "canary": canary_key})
    return crumbs

def check_canary_triggered(canary_key: str) -> bool:
    return False
