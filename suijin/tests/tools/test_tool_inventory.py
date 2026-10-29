"""Tool visibility — the classics were routed but invisible (2026-10-29
field complaint: "never seen AI using nmap, gobuster, katana").
"""


class TestClassicToolsInCatalog:
    def test_every_classic_is_visible_to_the_agent(self):
        from suijin.modules.agent.lib.prompts.tool_registry import _ALL_TOOLS, TOOL_REGISTRY

        classics = [
            "nmap_scan",
            "gobuster_dir",
            "gobuster_dns",
            "katana_crawl",
            "nikto_scan",
            "whatweb_scan",
            "feroxbuster_scan",
            "nuclei_scan",
            "ffuf_fuzz",
            "tool_inventory",
        ]
        missing = [c for c in classics if c not in _ALL_TOOLS or c not in TOOL_REGISTRY]
        assert not missing, f"invisible tools: {missing}"

    def test_catalog_prompt_renders_the_classics(self):
        from suijin.modules.agent.lib.prompts.tool_registry import build_tool_catalog_prompt

        prompt = build_tool_catalog_prompt("informational")
        for name in ("nmap_scan", "gobuster_dir", "katana_crawl", "ffuf_fuzz", "tool_inventory"):
            assert name in prompt, name


class TestToolInventory:
    def test_lists_catalog_tools_and_installed_binaries(self):
        from suijin.modules.tool_inventory.main import tool_inventory

        out = tool_inventory()
        assert "YOUR TOOLKIT" in out
        assert "Catalog tools" in out
        assert "Installed binaries" in out

    def test_installed_binary_listed_with_doctrine(self):
        """ffuf IS installed on this machine — it must appear with its line."""
        import shutil

        from suijin.modules.tool_inventory.main import tool_inventory

        out = tool_inventory()
        if shutil.which("ffuf"):
            assert "ffuf" in out.split("Installed binaries")[1].split("Not installed")[0]

    def test_missing_binaries_do_not_appear_as_installed(self, monkeypatch):
        import suijin.modules.tool_inventory.main as ti

        monkeypatch.setattr(ti.shutil, "which", lambda n: "/usr/local/bin/ffuf" if n == "ffuf" else None)
        out = ti.tool_inventory()
        installed_section = out.split("Installed binaries")[1].split("Not installed")[0]
        assert "ffuf" in installed_section
        assert "nmap" not in installed_section  # capability check, not a guess
        assert "nmap" in out.split("Not installed")[1]

    def test_never_fails_hard(self, monkeypatch):
        import suijin.modules.tool_inventory.main as ti

        monkeypatch.setattr("suijin.modules.agent.lib.prompts.tool_registry._ALL_TOOLS", None, raising=False)
        out = ti.tool_inventory()
        assert "YOUR TOOLKIT" in out  # degraded gracefully


class TestDispatchRoutes:
    def test_tool_inventory_routes(self):
        from suijin.server.tools.lib import dispatch

        out = dispatch.route_tool("tool_inventory", {}, config={})
        assert "YOUR TOOLKIT" in out
