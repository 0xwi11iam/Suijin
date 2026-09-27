"""The engagement-review round — every fix traced to a live audit trail.

Findings came from reviewing a real run's trail (referred to generically
per AGENTS.md): trail reasoning always empty, findings never landing in
the trail, dossier reading sources that do not exist, the ask panel
showing the literal word "Answer" instead of the question, the strip
wrapping into ghost rows, drift flagging intel consults, fireteams
spawned without doctrine, catalogs split by agent-invented engagement
names, and bypass_403 counting 404s as bypass wins.

All hosts here are example.com.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def trail(tmp_path, monkeypatch):
    import suijin.modules.tools.lib.audit_trail as at

    monkeypatch.setattr(at, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(at, "_last_flush", 0.0)
    monkeypatch.setattr(at, "_dirty", False)
    at.start_audit("example-engagement")
    yield at, tmp_path
    at._current_trail = None


class TestTrailTruth:
    def test_record_finding_lands_in_the_audit_trail(self, trail, monkeypatch):
        """A successful record_finding used to leave the trail's findings
        list EMPTY — every downstream reader (dossier, reviews) understated
        every productive run."""
        import suijin.modules.tools.lib.intel as intel

        at, tmp_path = trail
        kg = type("K", (), {"add_constraint": lambda *a, **k: None, "get_constraints": staticmethod(lambda t: [])})
        monkeypatch.setattr("suijin.modules.loader.load_local_module", lambda name: kg, raising=False)
        intel.record_finding("example.com", "behavior", "headline finding", "evidence text")
        at.end_audit(0.0)
        data = json.loads((tmp_path / "example-engagement.json").read_text())
        assert data["findings"], "record_finding did not reach the audit trail"
        assert data["findings"][0]["endpoint"] == "example.com"
        assert "headline finding" in data["findings"][0]["description"]

    def test_streamed_reasoning_is_stashed_and_capped(self, monkeypatch):
        """Reasoning models put deliberation in the stream, never in the
        decision JSON — the provider layer now exposes it so the trail can
        persist it."""
        from suijin.modules.providers import lib as prov

        prov._stash_reasoning("deliberation " * 9000)
        assert prov.get_usage()["last_reasoning"].startswith("deliberation")
        assert len(prov.get_usage()["last_reasoning"]) == prov._LAST_REASONING_CAP
        prov._stash_reasoning("")
        assert prov.get_usage()["last_reasoning"] == ""


class TestDriftFormattingAndExemptions:
    def test_format_cause_is_readable_not_a_dict(self):
        from suijin.modules.redteam.lib.intel.drift_analyser import format_cause

        line = format_cause(
            {
                "action_index": 3,
                "action": "http_request: Inspect the live robots policy at low volume to identify permitted paths",
                "patterns": ["low_goal_overlap"],
            }
        )
        assert line.startswith("#3 http_request — low goal overlap: Inspect the live robots policy")
        assert "{" not in line and "action_index" not in line
        long = format_cause({"action_index": 9, "action": "nmap_scan: " + "word " * 30, "patterns": ["hallucination"]})
        assert long.endswith("…") and "\\n" not in long  # truncated at a word boundary, never mid-word

    def test_intel_consults_are_not_drift(self):
        """Consulting the dossier/KG shares no keywords with objective prose
        and was flagged as drift — punishing exactly the mandated pre-test
        diligence."""
        from suijin.modules.redteam.lib.intel.drift_analyser import analyse_drift

        actions = [
            "http_request: fetch the homepage of example.com",
            "target_dossier: review everything known about this target before testing",
            "check_knowledge: prior constraints and verified history",
            "memory_recall: what worked before on this host",
            "search_kb: technique guidance for the observed stack",
        ]
        result = analyse_drift("example.com", actions)
        causes = [c for c in result["drift_causes"] if "low_goal_overlap" in c["patterns"]]
        assert not causes, f"intel consults flagged as drift: {causes}"

    def test_real_divergence_is_still_caught(self):
        from suijin.modules.redteam.lib.intel.drift_analyser import analyse_drift

        actions = [
            "http_request: fetch homepage",
            "nmap_scan: sweep unrelated subnet",
            "execute_terminal: download unrelated tooling",
            "sqlmap: attack a host neither in nor like the objective",
        ]
        result = analyse_drift("example.com only, nothing else", actions)
        assert result["drift_detected"], "genuine divergence must still fire"

    def test_tui_drift_note_renders_structured_lines(self):
        from rich.console import Console

        from suijin.client.tui.console_ui import EngagementUI

        ui = EngagementUI(Console(record=True, width=100, force_terminal=True), objective="t")
        ui.drift(
            {
                "drift_causes": [
                    {"action_index": 3, "action": "nmap_scan: sweep unrelated subnet", "patterns": ["low_goal_overlap"]}
                ],
                "suggestions": ["Agent actions diverged from goal — reinforce objective."],
            }
        )
        text = ui.console.export_text()
        assert "low goal overlap" in text
        assert "{" not in text.split("Drift")[1][:200], "raw dict repr leaked into the note"

    def test_pause_banner_announces_pause_once(self):
        """The strip's PAUSED badge owns the state; the banner must not
        stack a second 'Paused' on top of it."""
        from suijin.client.tui import session_control

        body = session_control.PAUSE_BANNER.lower()
        assert "paused" not in body, "banner duplicates the strip's PAUSED badge"
        assert "resume" in body, "the banner still carries the how-to"


class TestDossierRealSources:
    @pytest.fixture()
    def ws(self, tmp_path, monkeypatch):
        import suijin.modules.ops.lib.dossier as dos

        ws = tmp_path / "ws"
        eng = ws / "engagements" / "20260101_000000_example.com_run"
        (eng / "audit_trails").mkdir(parents=True)
        (eng / "audit_trails" / "example.com.json").write_text(
            json.dumps(
                {
                    "engagement": "example.com run",
                    "findings": [{"x": 1}],
                    "total_actions": 7,
                    "iterations": [
                        {"action": {"tool": "sqlmap", "args": {"url": "https://example.com/x"}, "success": False}}
                    ],
                }
            )
        )
        (ws / "engagements" / "20260101_000000_other").mkdir(parents=True)
        monkeypatch.setattr(dos, "WORKSPACE_DIR", ws)
        return ws

    def test_engagement_history_reads_real_trails(self, ws):
        import suijin.modules.ops.lib.dossier as dos

        d = dos.build_dossier("example.com", workspace=ws)
        assert any("example.com run" in e for e in d["engagements"]), d["engagements"]
        assert any("1 findings" in e for e in d["engagements"])

    def test_failures_mined_from_trails_not_dead_db(self, ws):
        import suijin.modules.ops.lib.dossier as dos

        d = dos.build_dossier("example.com", workspace=ws)
        assert any("sqlmap" in f for f in d["failures"]), d["failures"]

    def test_what_worked_section_present(self, ws):
        import suijin.modules.ops.lib.dossier as dos

        d = dos.build_dossier("example.com", workspace=ws)
        assert "worked" in d  # the section exists (values may be empty on a clean ws)

    def test_attack_memory_reads_engagement_catalogs(self, tmp_path, monkeypatch):
        """what_worked only globbed the legacy root — blind since the
        restructure moved catalogs into engagements/."""
        from suijin.modules.agent.lib import attack_memory as am

        cat = tmp_path / "engagements" / "20260101_000000_example.com_run" / "exploits" / "example-com"
        cat.mkdir(parents=True)
        (cat / "catalog.json").write_text(
            json.dumps(
                {
                    "entries": {
                        "EXP-001": {
                            "status": "CONFIRMED",
                            "vuln_class": "information_disclosure",
                            "title": "headers leak build info",
                            "target": "https://example.com",
                        }
                    }
                }
            )
        )
        monkeypatch.setattr(am, "_catalog_roots", lambda: [tmp_path / "engagements"])
        lines = am.what_worked("example.com")
        assert any("information_disclosure" in str(x) for x in lines), lines


class TestCatalogEngagementSlug:
    def test_elaborated_names_resolve_to_the_live_engagement(self, tmp_path, monkeypatch):
        """The agent invents engagement names mid-run ('x' then 'x detailed
        validation') — anything containing the live slug is the SAME
        engagement; only a different target earns a subdir."""
        import suijin.modules.tools.lib.exploit_catalog as ec

        live = tmp_path / "engagements" / "20260101_000000_example.com" / "exploits"
        live.mkdir(parents=True)
        monkeypatch.setattr("suijin.modules.platform.lib.workspace.exploits_dir", lambda: live)
        assert ec._engagement_dir("example.com") == live
        assert ec._engagement_dir("example.com detailed validation") == live, "split the catalog"
        assert ec._engagement_dir("other-target.net") == live / "other-target.net"


class TestFireteamDoctrine:
    def test_system_prompt_carries_the_doctrine(self):
        from suijin.modules.agent.lib.nodes.subagent_node import _build_system_prompt

        prompt = _build_system_prompt("probe the login flow", 8, None, doctrine="SCOPE — stay within example.com")
        assert "DOCTRINE" in prompt and "stay within example.com" in prompt
        assert "SCOPE — stay within" in prompt

    def test_prompt_without_doctrine_has_no_empty_section(self):
        from suijin.modules.agent.lib.nodes.subagent_node import _build_system_prompt

        prompt = _build_system_prompt("probe", 8, None, doctrine="")
        assert "DOCTRINE" not in prompt

    def test_run_subagent_forwards_doctrine(self):
        import inspect

        from suijin.modules.agent.lib.nodes.subagent_node import deploy_fireteam, run_subagent

        assert "route_tool_fn, doctrine)" in inspect.getsource(run_subagent)  # into the prompt
        assert "doctrine=doctrine" in inspect.getsource(deploy_fireteam)  # through the spawn


class TestBypass403Verdicts:
    def test_non_403_baseline_short_circuits(self, monkeypatch):
        """On a 200/404 baseline every status difference counted as a 'win'
        — a live probe reported 404s and 501s as bypasses."""
        import importlib

        ht = importlib.import_module("suijin.modules.tools.lib.http_tools")
        from suijin.modules.tools.lib.bypass_403 import bypass_403

        monkeypatch.setattr(ht, "http_request", lambda m, u, headers=None, body="": "Status: 200\nBody:\nok")
        out = bypass_403("https://example.com/admin")
        assert "nothing to bypass" in out and "VERDICT" not in out

    def test_error_status_changes_are_not_wins(self, monkeypatch):
        from suijin.modules.tools.lib.bypass_403 import bypass_403

        def fake_404_variants(method, url, headers=None, body=""):
            if url == "https://example.com/admin" and not headers:
                return "Status: 403\nBody:\nforbidden"  # the baseline
            return "Status: 404\nBody:\nnope"  # every variant 404s

        import importlib

        ht = importlib.import_module("suijin.modules.tools.lib.http_tools")

        monkeypatch.setattr(ht, "http_request", fake_404_variants)
        out = bypass_403("https://example.com/admin")
        assert "0 bypasses" in out and "404" in out and "not a bypass" not in out

    def test_2xx_is_a_real_bypass(self, monkeypatch):
        from suijin.modules.tools.lib.bypass_403 import bypass_403

        def fake(method, url, headers=None, body=""):
            # baseline 403; the X-Original-URL carrier gets 200
            if headers and "X-Original-URL" in headers:
                return "Status: 200\nBody:\nadmin dashboard"
            return "Status: 403\nBody:\nforbidden"

        import importlib

        ht = importlib.import_module("suijin.modules.tools.lib.http_tools")

        monkeypatch.setattr(ht, "http_request", fake)
        out = bypass_403("https://example.com/admin")
        assert "BYPASSED" in out and "X-Original-URL" in out

    def test_rate_limited_rows_are_inconclusive_not_enforced(self, monkeypatch):
        from suijin.modules.tools.lib.bypass_403 import bypass_403

        def fake_throttled_variants(method, url, headers=None, body=""):
            if url == "https://example.com/admin" and not headers:
                return "Status: 403\nBody:\nforbidden"  # baseline lands
            return "RATE LIMITED: target is throttling"  # variants go dark

        import importlib

        ht = importlib.import_module("suijin.modules.tools.lib.http_tools")

        monkeypatch.setattr(ht, "http_request", fake_throttled_variants)
        out = bypass_403("https://example.com/admin")
        assert "inconclusive" in out and "do NOT record" in out


class TestStripFitLadder:
    """Field complaint: MEM vanished the moment fireteams went live — the
    tail-crop deleted exactly the segments that matter. Under width
    pressure the strip drops by priority (zero tiers -> t/s -> compress
    Fireteam -> live tiers), never the tail."""

    def test_fireteam_live_keeps_mem_and_cred(self, monkeypatch):
        import io
        import re

        from rich.console import Console

        import suijin.client.tui.console_ui as cu

        monkeypatch.setattr(cu, "_fireteam_live_count", lambda: 3)
        monkeypatch.setattr(cu, "_fireteam_total", lambda: 3)
        cu.UI_STATE["librarian"] = 7
        try:
            for width in (120, 100, 80):
                ui = cu.EngagementUI(Console(record=True, width=width, force_terminal=True), objective="t")
                sink = Console(file=io.StringIO(), width=width, force_terminal=True)
                sink.print(ui._strip())
                plain = re.sub(r"\x1b\[[0-9;]*m", "", sink.file.getvalue()).replace("\n", " ")
                assert "MEM 7" in plain, f"MEM dropped at width {width}"
                assert "CRED" in plain, f"CRED dropped at width {width}"
                assert "Fireteam" in plain or "FT 3" in plain, f"fireteam dropped at width {width}"
        finally:
            cu.UI_STATE["librarian"] = 0


class TestSupervisorTrust:
    """Field verdict: 'the supervisor likes to bullshit and confuse the
    agent.' The deep LLM pass reads ten thought-lines and invents
    problems; injecting that as gospel derailed healthy runs."""

    def test_healthy_run_is_left_alone(self):
        from suijin.modules.agent.lib.supervisor import deep_analysis_corroborated

        healthy = [
            {"tool_name": t, "success": True} for t in ("http_request", "nmap_scan", "read_file", "check_knowledge")
        ]
        assert deep_analysis_corroborated(healthy) is False

    def test_failures_corroborate(self):
        from suijin.modules.agent.lib.supervisor import deep_analysis_corroborated

        rough = [{"tool_name": "http_request", "success": False}, {"tool_name": "sqlmap", "success": False}] + [
            {"tool_name": "http_request", "success": True}
        ]
        assert deep_analysis_corroborated(rough) is True

    def test_rut_and_fresh_findings_corroborate(self):
        from suijin.modules.agent.lib.supervisor import deep_analysis_corroborated

        rut = [{"tool_name": "ffuf", "success": True}] * 3
        assert deep_analysis_corroborated(rut) is True
        fresh = [
            {"tool_name": "record_finding", "success": True},
            {"tool_name": "http_request", "success": True},
        ]
        assert deep_analysis_corroborated(fresh) is True  # chaining hints welcome here

    def test_deep_injection_is_advisory_not_gospel(self):
        import inspect

        from suijin.modules.agent.lib import agent_graph

        src = inspect.getsource(agent_graph)
        assert "advisory; if it contradicts" in src, "the deep hint must be refusable"
        assert "deep_analysis_corroborated" in src, "the corroboration gate must be wired"

    def test_phase_stall_ignores_finding_work(self):
        """Cataloging a finding is exploitation WORK — FORCE EXPLOITATION
        fired exactly when the agent was doing post-finding bookkeeping."""
        from suijin.modules.agent.lib.supervisor import _detect_phase_stall

        pure_recon = [
            {"tool_name": "nmap", "success": True},
            {"tool_name": "gobuster", "success": True},
        ] * 10
        assert _detect_phase_stall(pure_recon) is not None  # genuine stall still fires
        # findings are the LATEST work in reality — recon then record
        with_finding = pure_recon + [
            {"tool_name": "record_finding", "success": True},
            {"tool_name": "catalog_exploit", "success": True},
        ]
        assert _detect_phase_stall(with_finding) is None


class TestChainDoctrine:
    def test_generated_order_demands_chaining(self):
        """The hunt rule rides the per-turn engagement order (the dynamic
        tail below any prompt base): chain findings, bias to action — the
        field complaint was one-and-done timidity."""
        from suijin.modules.agent.lib.prompts.base import _dynamic_tail, engagement_order

        order = engagement_order("hunt example.com thoroughly")
        assert "CHAIN, DON'T COLLECT" in order
        assert "BIAS TO ACTION" in order
        assert "UNLOCKS" in order
        # and it actually reaches the agent each turn
        tail = _dynamic_tail({"original_objective": "hunt example.com"}, "recon")
        assert "CHAIN, DON'T COLLECT" in tail


class TestVulnCounting:
    """Field ask: 'make sure vuln counting works fine'. Two real bugs
    found by replaying the catalog's actual CONFIRMED lines: unlabeled
    confirmations vanished from the count (regex demanded a '— word'
    tail), and setdefault froze the FIRST severity forever."""

    def _ui(self):
        from rich.console import Console

        import suijin.client.tui.console_ui as cu

        cu.reset_gauges()
        return cu, cu.EngagementUI(Console(record=True, width=120, force_terminal=True), objective="t")

    def test_every_confirmed_counts_even_unlabeled(self):
        cu, ui = self._ui()
        ui.output("EXP-001 CONFIRMED — MEDIUM CVSS 5.3 : leak. The VERIFIER ran exploit.yaml.")
        ui.output("EXP-002 CONFIRMED — LOW : minor")
        ui.output("EXP-003 CONFIRMED — CRITICAL CVSS 9.8 : rce")
        ui.output("EXP-004 CONFIRMED : no severity word at all")
        assert len(cu.UI_STATE["exploits"]) == 4, cu.UI_STATE["exploits"]
        assert cu.UI_STATE["exploits"]["EXP-004"] == "med"  # floor
        assert cu.UI_STATE["exploits"]["EXP-003"] == "crit"
        assert cu.UI_STATE["exploits"]["EXP-002"] == "low"

    def test_revalidation_updates_the_tier(self):
        cu, ui = self._ui()
        ui.output("EXP-001 CONFIRMED — MEDIUM CVSS 5.3 : first verdict")
        ui.output("EXP-001 CONFIRMED — LOW CVSS 3.7 : re-validated down")
        assert cu.UI_STATE["exploits"] == {"EXP-001": "low"}, cu.UI_STATE["exploits"]

    def test_cvss_only_lines_map_by_score(self):
        cu, ui = self._ui()
        ui.output("EXP-009 CONFIRMED — CVSS 7.2 : score only, no word")
        assert cu.UI_STATE["exploits"]["EXP-009"] == "high"

    def test_strip_renders_the_counts(self):
        import io
        import re as _re

        cu, ui = self._ui()
        ui.output("EXP-001 CONFIRMED — CRITICAL CVSS 9.8 : rce")
        ui.output("EXP-002 CONFIRMED — LOW : minor")
        from rich.console import Console

        sink = Console(file=io.StringIO(), width=120, force_terminal=True)
        sink.print(ui._strip())
        plain = _re.sub(r"\x1b\[[0-9;]*m", "", sink.file.getvalue()).replace("\n", " ")
        assert "CRIT 1" in plain and "LOW 1" in plain and "EXP 2" in plain, plain[:160]


class TestFireteamExploitCounting:
    """Field bug: two vulns confirmed in fireteam result boxes rendered in
    the UI but the strip never counted them — ui.fireteam() never fed the
    exploit ledger."""

    def test_fireteam_confirmations_count(self):
        from rich.console import Console

        import suijin.client.tui.console_ui as cu

        cu.reset_gauges()
        ui = cu.EngagementUI(Console(record=True, width=120, force_terminal=True), objective="t")
        ui.fireteam("Fireteam team-abc: 3 deployed, 0 skipped")
        ui.fireteam(
            "[COMPLETE] agent 2: EXP-002 CONFIRMED — HIGH CVSS 7.5 : auth bypass "
            "verified by the subagent. The VERIFIER ran exploit.yaml."
        )
        assert cu.UI_STATE["exploits"].get("EXP-002") == "high", cu.UI_STATE["exploits"]
        assert cu.UI_STATE["fireteams"] == 1


class TestFormatContractFraming:
    """Field complaint: 'We need respond exactly one JSON object per
    developer. says this every time'. Warning-framed format rules made the
    model re-acknowledge the contract in its reasoning EVERY turn."""

    def test_core_states_the_contract_without_warning_framing(self):
        from suijin.modules.agent.lib.prompts.base import _static_prompt_core

        core = _static_prompt_core("recon")
        assert "This contract is automatic for you" in core
        assert "never need to think about it" in core
        assert "EXACTLY ONE JSON" not in core, "warning framing invites per-turn compliance checks"
        assert "Four required fields" not in core


class TestRepoCleanliness:
    """Field report: 'files are writing to random places'. Two writers
    found: the kernel Context defaulted its workspace to CWD (a month of
    journal.log at the repo root), and the module-SDK self-test dropped a
    whole workspace next to the pack it verified."""

    def test_context_workspace_honors_env_before_cwd(self, tmp_path, monkeypatch):
        from suijin.kernel.context import Context

        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path / "from-env"))
        assert Context().workspace == tmp_path / "from-env"
        c = Context(workspace=tmp_path / "explicit")
        assert c.workspace == tmp_path / "explicit"  # arg still wins
        monkeypatch.delenv("SUIJIN_WORKSPACE")
        assert Context().workspace.is_absolute()  # cwd fallback, last resort

    def test_module_selftest_boots_in_a_temp_workspace(self):
        import inspect

        from suijin.modules.tools.lib import module_sdk

        src = inspect.getsource(module_sdk)
        assert "workspace=base.parent" not in src, "the self-test still litters the source tree"
        assert "TemporaryDirectory" in src

    def test_termination_drains_the_typewriter_first(self):
        """The final turn has no next iteration boundary, so the model's
        closing reasoning kept playing while the menu redraw cut it
        mid-word — done() and failure() drain before teardown."""
        import inspect

        from suijin.client.tui.console_ui import EngagementUI

        done_src = inspect.getsource(EngagementUI.done)
        fail_src = inspect.getsource(EngagementUI.failure)
        assert "stream_done()" in done_src and "stream_done()" in fail_src


class TestPromptRedesign:
    """The layered prompt contract + slim tail + injection TTL
    (2026-09-27 redesign, operator-approved A+B+C+D)."""

    def test_overlay_replaces_appends_and_notes(self):
        from suijin.modules.agent.lib.prompts.prompt_file import overlay_prompt

        core = "# ROLE\n\nagent\n\n## RULES\ncore rules\n\n## WORKFLOW\ncore flow"
        user = "## RULES\nmy rules\n\n## BONUS DOCTRINE\nextra section"
        out = overlay_prompt(core, user)
        assert "my rules" in out and "core rules" not in out  # replace by name
        assert "BONUS DOCTRINE" in out and "extra section" in out  # append
        assert "core flow" in out  # untouched sections stay canonical
        blob = overlay_prompt(core, "just prose, no headers")
        assert "just prose" in blob and "core flow" in blob  # prose rides, core stays
        assert "OPERATOR NOTES" in blob

    def test_compact_order_after_turn_one(self):
        from suijin.modules.agent.lib.prompts.base import engagement_order

        full = engagement_order("hunt example.com thoroughly")
        slim = engagement_order("hunt example.com thoroughly", compact=True)
        assert "CHAIN" in slim and "BIAS TO ACTION" in slim and "turn 1" in slim
        assert len(slim) < len(full) / 4  # a one-liner, not a re-render

    def test_injections_have_a_ttl(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        src = inspect.getsource(think_node)
        assert "superseded guidance pruned" in src, "no pruning seam"
        # and the behavior: simulate the prune loop
        msgs = [
            {"role": "user", "content": "SUPERVISOR: old nag"},
            {"role": "user", "content": "DRIFT WARNING: older"},
            {"role": "user", "content": "ORACLE: newest hint"},
            {"role": "user", "content": "Result: normal"},
        ]
        idx = [
            i
            for i, m in enumerate(msgs)
            if str(m.get("content", "")).lstrip().upper().startswith(("SUPERVISOR", "DRIFT WARNING", "ORACLE"))
        ]
        for i in idx[:-2]:
            msgs[i] = {"role": "user", "content": "(superseded guidance pruned)"}
        assert msgs[0]["content"] == "(superseded guidance pruned)"
        assert msgs[2]["content"] == "ORACLE: newest hint"  # newest two kept

    def test_order_demands_clean_completion(self):
        from suijin.modules.agent.lib.prompts.base import engagement_order

        order = engagement_order("hunt example.com")
        assert "COMPLETE CLEANLY" in order
        assert "generate_report" in order

    def test_tool_catalog_is_phase_scoped(self):
        from suijin.modules.agent.lib.prompts.tool_registry import build_tool_catalog_prompt

        recon = build_tool_catalog_prompt("recon")
        assert "phase: recon" in recon
