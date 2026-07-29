"""GVM/OpenVAS integration via GMP protocol."""
import subprocess, shlex, json, os

def _gvm_exec(cmd):
    """Run gvm-cli command. Requires GVM environment setup."""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600, cwd="/tmp")
    return r.stdout or r.stderr or "(no output)"

def gvm_scan(target, config="Full and fast"):
    if not target: return "Error: target required"
    return _gvm_exec(f"gvm-cli socket --xml '<create_task><name>Medusa-{target}</name><config><name>{config}</name></config><target><name>{target}</name></target></create_task>' 2>&1")

def gvm_list_tasks():
    return _gvm_exec("gvm-cli socket --xml '<get_tasks/>' 2>&1")

def gvm_get_results(task_id):
    if not task_id: return "Error: task_id required"
    return _gvm_exec(f"gvm-cli socket --xml '<get_results filter=\"task_id={task_id}\"/>' 2>&1")