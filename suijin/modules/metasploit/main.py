import importlib.util, os


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:  # standalone fallback
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


_tools = None


def _get_tools():
    global _tools
    if _tools is None:
        # CANONICAL import — the old spec_from_file_location force-load
        # created a SECOND dispatch module instance (independent
        # repeat-guard ledgers, drift hazard) shadowing core routes
        from suijin.modules.tools.lib import dispatch as _dispatch_mod

        _tools = _dispatch_mod
    return _tools


def msf_check(config=None):
    import json

    with open(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "suijin", "config.json"))) as f:
        cfg = json.load(f)
    return _get_tools().msf_check(cfg)


def msf_command(cmd=None, command=None):
    import json

    with open(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "suijin", "config.json"))) as f:
        cfg = json.load(f)
    return _get_tools().msf_command(cmd or command or "", cfg)


def msf_run(module, payload=None, options=None):
    import json

    with open(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "suijin", "config.json"))) as f:
        cfg = json.load(f)
    return _get_tools().msf_run(module, payload, options or {}, cfg)


def msf_sessions(action="list", id=None):
    import json

    with open(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "suijin", "config.json"))) as f:
        cfg = json.load(f)
    return _get_tools().msf_sessions(action, id, cfg)
