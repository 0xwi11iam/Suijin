"""Addons — zero-boilerplate tool drops.

Contract: a folder with main.py under the addons root makes every public
callable an agent tool at boot — reachable through dispatch.route_tool
(the agent's router) AND ctx.call_tool (kernel/MCP), and advertised in
the tool catalog. Underscore folders stay dormant. `module adopt`
graduates an addon to a full pack.
"""

from pathlib import Path

from suijin.modules.addons.entry import scan_addons


def _write_addon(root: Path, name: str, code: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(code)


class TestScan:
    def test_public_functions_become_tools(self, tmp_path):
        _write_addon(
            tmp_path,
            "demo",
            '''def ping(host: str = "") -> str:
    """Ping a host."""
    return f"pong {host}"


def _private():
    return "hidden"


import json  # imported names must NOT register
''',
        )
        addons = scan_addons([tmp_path])
        assert "demo" in addons
        assert set(addons["demo"]) == {"ping"}
        assert addons["demo"]["ping"]["description"] == "Ping a host."
        assert addons["demo"]["ping"]["params"] == ["host"]

    def test_underscore_dirs_dormant(self, tmp_path):
        _write_addon(tmp_path, "_draft", "def x() -> str:\n    return '1'\n")
        assert scan_addons([tmp_path]) == {}

    def test_broken_addon_skipped_not_fatal(self, tmp_path):
        _write_addon(tmp_path, "broken", "this is not python(\n")
        _write_addon(tmp_path, "good", "def ok() -> str:\n    '''Fine.'''\n    return 'ok'\n")
        addons = scan_addons([tmp_path])
        assert "good" in addons and "broken" not in addons

    def test_catalog_section(self, tmp_path):
        _write_addon(
            tmp_path, "demo", 'def ping(host: str = "") -> str:\n    """Ping a host."""\n    return f"pong {host}"\n'
        )
        import suijin.modules.addons.entry as entry

        orig = entry.addon_roots
        entry.addon_roots = lambda: [tmp_path]
        try:
            text = entry.catalog_text()
        finally:
            entry.addon_roots = orig
        assert "## Addon Tools" in text and "**ping**" in text


class TestBootGate:
    def test_drop_addon_boot_tool_callable_everywhere(self, tmp_path, monkeypatch):
        """THE gate: write main.py -> boot -> the tool answers through
        BOTH the kernel surface and the agent's dispatch router, and the
        catalog advertises it."""
        import suijin.modules.addons.entry as entry

        _write_addon(
            tmp_path, "gatekit", 'def echo(text: str = "") -> str:\n    """Echo text."""\n    return f"echo:{text}"\n'
        )
        monkeypatch.setattr(entry, "addon_roots", lambda: [tmp_path])

        from suijin.kernel import controller

        ctx, report = controller.boot(
            module_roots=[Path(__file__).resolve().parents[3] / "suijin" / "modules"],
            workspace=tmp_path / "ws",
            quiet=True,
        )
        assert any(u.id == "addons" for u in report.boot_order)
        # kernel surface
        assert ctx.call_tool("echo", {"text": "hi"}) == "echo:hi"
        # agent router (dispatch)
        from suijin.modules.tools.lib import dispatch

        routes = dispatch._build_routes(None)
        assert "echo" in routes
        assert routes["echo"]({"text": "yo"}) == "echo:yo"
        # catalog advertises it
        assert "**echo**" in dispatch.get_tool_catalog()
        ctx.shutdown()


class TestAdopt:
    def test_adopt_creates_bootable_pack(self, tmp_path):
        from suijin.modules.tools.lib.module_sdk import adopt_addon

        _write_addon(tmp_path, "grad", 'def tool_a(x: str = "") -> str:\n    """Does A."""\n    return f"a:{x}"\n')
        dest = adopt_addon("grad", addon_root=tmp_path, dest_root=tmp_path / "packs")
        for f in ("manifest.json", "plugin.json", "entry.py", "main.py", "skill.md", "__init__.py"):
            assert (dest / f).exists(), f
        # the adopted pack boots as a kernel unit
        from suijin.kernel import controller

        ctx, report = controller.boot(
            module_roots=[tmp_path / "packs", Path(__file__).resolve().parents[3] / "suijin" / "modules"],
            workspace=tmp_path / "ws2",
            quiet=True,
        )
        assert any(u.id == "grad" for u in report.boot_order)
        assert ctx.call_tool("tool_a", {"x": "1"}) == "a:1"
        ctx.shutdown()
