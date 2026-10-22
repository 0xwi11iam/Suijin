"""tool manifest tests — the declarative restructure (2026-10-20).

Every tool declares platforms + prereqs + ordered fallback chains +
how presence is verified; prereqs are KNOWN before installs run, never
discovered by failure. Platform vocabulary matches the pack manifests
(macos · linux · kali · windows).
"""


class TestManifestShape:
    def test_every_tool_has_platforms_and_chains(self):
        from suijin.modules.tools.lib.tool_manifest import manifest

        for name, rec in manifest().items():
            assert rec["platforms"], name
            if rec.get("built_in"):
                assert not rec["install"]  # nothing to install — that's the point
                continue
            for plat in rec["platforms"]:  # a chain for every DECLARED platform
                assert rec["install"].get(plat), f"{name} missing {plat} chain"
            # and never a chain for an undeclared platform
            for plat in rec["install"]:
                assert plat in rec["platforms"], f"{name} has a {plat} chain but doesn't declare it"

    def test_apt_lines_are_automation_safe(self):
        """No apt line without -y — the human-hint hang, structurally
        impossible now."""
        from suijin.modules.tools.lib.tool_manifest import manifest

        for name, rec in manifest().items():
            for plat, chain in rec["install"].items():
                for ln in chain:
                    if "apt install" in ln:
                        assert " -y" in ln or "-y " in ln, f"{name}/{plat}: {ln}"

    def test_platforms_derivation(self):
        from suijin.modules.tools.lib.tool_manifest import platforms_of

        assert set(platforms_of("nmap")) == {"kali", "linux", "macos"}

    def test_platform_vocabulary_is_canonical(self):
        from suijin.modules.tools.lib.tool_manifest import normalize_platform, platforms_of

        assert "macos" in platforms_of("nmap")
        assert "mac" not in platforms_of("nmap")
        assert normalize_platform("mac") == "macos"
        assert normalize_platform("darwin") == "macos"

    def test_override_only_tool_present(self):
        """Tools declared ONLY in overrides (absent from the raw hint
        table) still get full records."""
        from suijin.modules.tools.lib.tool_manifest import manifest, prereqs_of

        assert "wpscan" in manifest()
        assert "gem" in prereqs_of("wpscan")  # the gem fallback declares it

    def test_builtin_has_no_install_chains(self):
        from suijin.modules.tools.lib.tool_manifest import manifest

        rec = manifest()["curl"]
        assert rec.get("built_in") is True
        assert rec["install"] == {}


class TestPrereqs:
    def test_go_tools_declare_go(self):
        from suijin.modules.tools.lib.tool_manifest import prereqs_of

        assert "go" in prereqs_of("ffuf")
        assert "go" in prereqs_of("nuclei")

    def test_apt_first_no_prereq(self):
        from suijin.modules.tools.lib.tool_manifest import prereqs_of

        assert prereqs_of("nmap") == []

    def test_pip_tool_declares_pip(self):
        from suijin.modules.tools.lib.tool_manifest import prereqs_of

        assert prereqs_of("wafw00f") == ["pip"]

    def test_runtime_only_tools_still_probe_apt(self):
        """Regression (full-box build 2026-10-22): when a tool had only a
        runtime hint and no apt key, the chain skipped the generic apt
        probe — so Kali's real packages for trufflehog/mitmproxy were
        never tried. Line 1 must be the apt attempt."""
        from suijin.modules.tools.lib.tool_manifest import install_plan

        assert "apt install" in install_plan("mitmproxy", "kali")[0]
        assert "apt install" in install_plan("trufflehog", "kali")[0]

    def test_prereqs_for_set(self):
        from suijin.modules.tools.lib.tool_manifest import prereqs_for

        assert prereqs_for(["nmap", "nuclei", "subfinder"]) == {"go"}


class TestFallbackChains:
    def test_ffuf_kali_chain(self):
        from suijin.modules.tools.lib.tool_manifest import install_plan

        chain = install_plan("ffuf", "kali")
        assert chain[0].startswith("sudo apt install -y")
        assert any(ln.startswith("go install") for ln in chain)

    def test_macos_chain_uses_brew(self):
        from suijin.modules.tools.lib.tool_manifest import install_plan

        assert install_plan("nmap", "macos")[0].startswith("brew")

    def test_known_tool_on_undeclared_platform_has_no_plan(self):
        from suijin.modules.tools.lib.tool_manifest import install_plan

        # testssl.sh is declared for linux/kali only — macOS gets no plan
        assert install_plan("testssl.sh", "macos") == []

    def test_unknown_tool_gets_generic_attempt(self):
        from suijin.modules.tools.lib.tool_manifest import install_plan

        chain = install_plan("warpdrive", "kali")
        assert chain and "warpdrive" in chain[0]

    def test_az_and_gcloud_have_kali_paths(self):
        """Full-box build 2026-10-22: both were declared macOS-only and
        failed as 'unsupported'. Kali packages azure-cli; gcloud has an
        official tarball."""
        from suijin.modules.tools.lib.tool_manifest import install_plan, platforms_of

        for name in ("az", "gcloud"):
            assert "kali" in platforms_of(name)
            assert install_plan(name, "kali"), name

    def test_playwright_installs_headless_shell_only(self):
        from suijin.modules.tools.lib.tool_manifest import install_plan

        assert "--only-shell" in install_plan("playwright", "kali")[0]

    def test_trufflehog_chain_has_vendor_fallback(self):
        from suijin.modules.tools.lib.tool_manifest import install_plan

        assert "install.sh" in " ".join(install_plan("trufflehog", "kali"))

    def test_dirsearch_chain_installs_pkg_resources_shim(self):
        """Full-box smoke 2026-10-22: apt dirsearch died on import
        (pkg_resources is gone from setuptools 81+/Python 3.14)."""
        from suijin.modules.tools.lib.tool_manifest import install_plan

        assert "setuptools<81" in install_plan("dirsearch", "kali")[0]


class TestVerify:
    def test_binary_alias(self):
        from suijin.modules.tools.lib.tool_manifest import verify_of

        assert verify_of("testssl.sh") == ("binary", "testssl")

    def test_alternate_binaries(self):
        """Kali's apt crackmapexec ships `crackmapexec`; pipx NetExec
        ships `nxc` — either validates the tool."""
        from suijin.modules.tools.lib.tool_manifest import verify_checks

        values = [v for _, v in verify_checks("crackmapexec")]
        assert "crackmapexec" in values and "nxc" in values

    def test_python_module_for_pip_only_dep(self):
        from suijin.modules.tools.lib.tool_manifest import verify_of

        assert verify_of("requests") == ("python", "requests")

    def test_dashed_python_module(self):
        from suijin.modules.tools.lib.tool_manifest import verify_of

        assert verify_of("duckduckgo-search") == ("python", "duckduckgo_search")


class TestPlatformSupport:
    def test_linux_covers_kali(self):
        from suijin.modules.tools.lib.tool_manifest import platform_supported

        assert platform_supported(["linux"], "kali")
        assert not platform_supported(["kali"], "linux")  # apt-kali-only is stricter

    def test_all_covers_everything(self):
        from suijin.modules.tools.lib.tool_manifest import platform_supported

        for host in ("kali", "linux", "macos", "windows"):
            assert platform_supported(["all"], host)

    def test_pack_vocabulary(self):
        from suijin.modules.tools.lib.tool_manifest import platform_supported

        assert platform_supported(["macos", "linux"], "macos")
        assert not platform_supported(["macos", "linux"], "windows")


class TestPackDeclarations:
    def test_tool_install_block_merges(self, monkeypatch):
        """A pack can declare install metadata for its own dependency."""
        from suijin.modules.tools.lib import tool_manifest as tm

        packed = {
            "packtool": {
                "platforms": ["kali"],
                "kali": ["sudo apt install -y --no-install-recommends packtool"],
                "verify": {"kind": "binary", "value": "packbin"},
            }
        }
        monkeypatch.setattr(tm, "_pack_records", lambda: packed)
        try:
            rec = tm.manifest(refresh=True)["packtool"]
            assert rec["platforms"] == ["kali"]
            assert tm.verify_of("packtool") == ("binary", "packbin")
            assert tm.install_plan("packtool", "kali")[0].endswith("packtool")
        finally:
            tm._MANIFEST_CACHE = None


class TestProvisionPlan:
    """The box plan consumes the manifest, refined by one apt probe."""

    def _plan(self, monkeypatch, tools, probe):
        from suijin.server.box import provision as prov

        monkeypatch.setattr(prov, "_probe_apt_candidates", lambda packages: probe)
        return prov._resolve_plan(tools)

    def test_apt_confirmed(self, monkeypatch):
        plan = self._plan(monkeypatch, ["ffuf"], {"ffuf"})
        assert plan["ffuf"][0] == "apt"
        assert "apt install" in plan["ffuf"][1]

    def test_fallback_when_package_missing(self, monkeypatch):
        plan = self._plan(monkeypatch, ["ffuf"], set())
        rt, line = plan["ffuf"]
        assert rt == "go"
        assert line.startswith("go install")

    def test_failed_probe_trusts_the_manifest(self, monkeypatch):
        plan = self._plan(monkeypatch, ["ffuf"], None)
        assert plan["ffuf"][0] == "apt"

    def test_pip_fallback_is_box_safe(self, monkeypatch):
        plan = self._plan(monkeypatch, ["wafw00f"], set())
        rt, line = plan["wafw00f"]
        assert rt == "pip"
        assert "--user" in line and "--break-system-packages" in line

    def test_gem_fallback_uses_sudo(self, monkeypatch):
        plan = self._plan(monkeypatch, ["wpscan"], set())
        rt, line = plan["wpscan"]
        assert rt == "gem"
        assert line.startswith("sudo gem install")

    def test_builtin_plans_a_noop(self, monkeypatch):
        plan = self._plan(monkeypatch, ["curl"], set())
        assert plan["curl"] == ("builtin", "true")

    def test_runtimes_follow_the_chosen_lines(self, monkeypatch):
        from suijin.server.box import provision as prov

        plan = self._plan(monkeypatch, ["ffuf"], set())
        assert prov._runtimes_for(plan) == {"go"}
        assert prov._RUNTIME_PKGS["go"]
        assert prov._RUNTIME_PKGS["pip"]  # pip is installable, not implicit

    def test_presence_script_uses_declared_verify(self):
        from suijin.server.box.provision import presence_script

        script = presence_script(["requests", "testssl.sh", "nmap"])
        assert 'python3 -c "import requests"' in script
        assert "command -v testssl" in script
        assert "command -v nmap" in script

    def test_presence_script_accepts_either_binary(self):
        from suijin.server.box.provision import presence_script

        script = presence_script(["crackmapexec"])
        assert "command -v nxc" in script
        assert "command -v crackmapexec" in script

    def test_fallthrough_attempts_are_declared(self):
        """Real fall-through: if the chosen installer fails, the vendor
        script is still tried (trufflehog's go module can't build)."""
        from suijin.server.box.provision import _fallback_attempts

        attempts = _fallback_attempts("trufflehog", "sudo apt install -y --no-install-recommends trufflehog")
        cmds = [c for _, c in attempts]
        assert cmds and all("apt install" not in c for c in cmds)
        assert any("install.sh" in c for c in cmds)


class TestEnginePicker:
    def test_env_override_skips_prompt(self, monkeypatch):
        monkeypatch.setenv("SUIJIN_CONTAINER_ENGINE", "docker")
        from suijin.server.box import engine as bx

        assert bx.detect_engine(interactive=True) == "docker"

    def test_persisted_config_wins_over_prompt(self, monkeypatch):
        """The bug this test exists for: a saved choice must never be
        re-asked — config is consulted BEFORE the interactive pick."""
        from suijin.server.box import engine as bx

        monkeypatch.delenv("SUIJIN_CONTAINER_ENGINE", raising=False)
        import shutil

        monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/present")
        monkeypatch.setattr(bx, "_stdin_is_tty", lambda: True)
        monkeypatch.setattr(bx, "_configured_engine", lambda: "docker")
        monkeypatch.setattr(bx, "_prompt_engine", lambda: (_ for _ in ()).throw(AssertionError("prompted")))
        assert bx.detect_engine(interactive=True) == "docker"

    def test_prompt_persists_only_a_valid_choice(self, monkeypatch):
        from suijin.server.box import engine as bx

        monkeypatch.delenv("SUIJIN_CONTAINER_ENGINE", raising=False)
        import shutil

        monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/present")
        monkeypatch.setattr(bx, "_stdin_is_tty", lambda: True)
        monkeypatch.setattr(bx, "_configured_engine", lambda: "")
        monkeypatch.setattr(bx, "_prompt_engine", lambda: "docker")
        saved: list[str] = []
        monkeypatch.setattr(bx, "_persist_engine", saved.append)
        assert bx.detect_engine(interactive=True) == "docker"
        assert saved == ["docker"]
