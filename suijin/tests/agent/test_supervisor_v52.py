"""Supervisor v5.2 — helps exploitation instead of interfering."""

import importlib

import pytest

sup = importlib.import_module("suijin.modules.agent.lib.supervisor")


@pytest.fixture(autouse=True)
def _clean_cooldowns():
    sup._TACTICAL_COOLDOWN.clear()
    sup._last_fired.clear()
    yield
    sup._TACTICAL_COOLDOWN.clear()
    sup._last_fired.clear()


class TestAntiInterference:
    def test_unverified_claim_no_phantom_tool(self):
        """Old bug: guidance demanded diff_responses — a tool that does not
        exist. Now it names real tools and never mentions the phantom."""
        trace = [
            {"tool_name": "write_note", "thought": "note"},
            {"tool_name": "write_note", "thought": "note"},
            {"tool_name": "http_request", "thought": "SQLi found in /search"},
        ]
        g = sup._detect_unverified_claim(trace)
        assert g is not None
        assert "diff_responses" not in g
        assert "diff_response" in g or "evidence_capture" in g

    def test_unverified_claim_accepts_real_evidence(self):
        trace = [
            {"tool_name": "http_request", "thought": "XSS detected in q param"},
            {"tool_name": "evidence_capture", "thought": "capturing"},
            {"tool_name": "write_note", "thought": "note"},
        ]
        assert sup._detect_unverified_claim(trace) is None

    def test_successful_repro_counts_as_verification(self):
        trace = [
            {"tool_name": "http_request", "thought": "SQLi found"},
            {"tool_name": "http_request", "thought": "repro", "success": True},
        ]
        assert sup._detect_unverified_claim(trace) is None

    def test_found_but_not_exploited_ignores_thought_mentions(self):
        """Old bug: 'checking for XSS' in a thought counted as a finding."""
        trace = [
            {"tool_name": "http_request", "thought": "checking for XSS in the search param"},
            {"tool_name": "search_cve", "thought": "researching"},
            {"tool_name": "write_note", "thought": "note"},
            {"tool_name": "search_kb", "thought": "kb"},
        ]
        assert sup._detect_found_but_not_exploited(trace) is None

    def test_found_but_not_exploited_fires_on_confirmed(self):
        trace = [
            {"tool_name": "http_request", "thought": "SQLi confirmed on /search"},
            {"tool_name": "write_note", "thought": "note"},
            {"tool_name": "write_note", "thought": "note"},
            {"tool_name": "write_note", "thought": "note"},
        ]
        g = sup._detect_found_but_not_exploited(trace)
        assert g is not None and "TEST" in g

    def test_exploitation_in_progress_not_interrupted(self):
        """Any injection-class tool after the finding = hands off."""
        trace = [
            {"tool_name": "http_request", "thought": "SSRF confirmed"},
            {"tool_name": "sqlmap_scan", "thought": "automating extraction"},
            {"tool_name": "gobuster_dir", "thought": "more surface"},
        ]
        assert sup._detect_found_but_not_exploited(trace) is None

    def test_research_is_not_bookkeeping(self):
        """Old bug: search_cve/web_search/read_file counted as bookkeeping —
        researching an exploit for 4 turns got slapped."""
        trace = [
            {"tool_name": "search_cve", "thought": "cve"},
            {"tool_name": "web_search", "thought": "poc"},
            {"tool_name": "read_file", "thought": "poc file"},
            {"tool_name": "write_file", "thought": "adapting exploit"},
        ]
        assert sup._detect_bookkeeping_loop(trace) is None

    def test_duplicate_probing_is_not_no_progress(self):
        """Re-probing one endpoint with evolving payloads IS exploitation —
        productivity marks those 'duplicate' and the old detector fired."""
        trace = [
            {
                "tool_name": "http_request",
                "thought": "payload v2",
                "success": True,
                "productivity": {"verdict": "duplicate"},
            },
        ] * 5
        assert sup._detect_no_progress(trace) is None

    def test_cooldown_prepeats_repeated_nagging(self):
        """A detector that fired stays quiet for 15 iterations."""
        trace = [
            {"tool_name": "write_note", "thought": "n"},
            {"tool_name": "job_list", "thought": "j"},
            {"tool_name": "job_status", "thought": "s"},
            {"tool_name": "check_knowledge", "thought": "c"},
        ]
        g1 = sup.analyze_trace(trace, iteration=10)
        assert g1 is not None
        g2 = sup.analyze_trace(trace, iteration=11)
        assert g2 is None  # cooled down
        g3 = sup.analyze_trace(trace, iteration=30)
        assert g3 is not None  # cooldown expired


class TestTacticalLibrary:
    def test_count_and_shape(self):
        assert len(sup.TACTICAL_FOLLOWUPS) >= 50
        for e in sup.TACTICAL_FOLLOWUPS:
            assert e["id"] and e["hint"] and e["followups"], e
            assert hasattr(e["signal"], "search")  # compiled

    def test_jwt_hint_fires_then_cools(self):
        tok = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.dozjgNryP4J3jVmNHl0w5N65IWDpmNfXPU4HuXqoj0k"
        trace = [{"tool_name": "http_request", "tool_output": f"Bearer {tok}"}]
        h = sup._tactical_check(trace)
        assert h and "jwt_inspect" in h
        assert sup._tactical_check(trace) is None

    def test_followup_done_silences_hint(self):
        trace = [
            {"tool_name": "http_request", "tool_output": "key=AIzaSyANwe_3zpMHBwFvCwC3vqyp0A4PUDWrsKw"},
            {"tool_name": "google_key_probe", "tool_output": "probed"},
        ]
        assert sup._tactical_check(trace) is None

    def test_sqli_confirmed_prompts_extraction(self):
        trace = [{"tool_name": "http_request", "tool_output": "error: You have an error in your SQL syntax"}]
        h = sup._tactical_check(trace)
        assert h and "sqlmap" in h.lower()

    def test_tactical_via_analyze_trace_when_healthy(self):
        tok = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.dozjgNryP4J3jVmNHl0w5N65IWDpmNfXPU4HuXqoj0k"
        trace = [
            {"tool_name": "http_request", "thought": "auth flow", "success": True, "tool_output": f"token {tok}"},
            {"tool_name": "http_request", "thought": "next", "success": True, "tool_output": "ok"},
        ]
        g = sup.analyze_trace(trace, iteration=10)
        assert g and "jwt_inspect" in g


class TestOracleGating:
    def test_oracle_fires_for_http_responses_only(self):
        """The oracle triages HTTP responses; the field run fired it on a
        518KB JS-bundle output and injected an irrelevant SQLi hypothesis."""
        import inspect

        from suijin.modules.agent.lib import agent_graph

        src = inspect.getsource(agent_graph.SuijinAgentGraph._think)
        assert 'last_step.get("tool_name") == "http_request"' in src


class TestInstantPause:
    def test_sigint_handler_raises(self):
        import inspect

        from suijin.modules.redteam.lib import redteamer

        src = inspect.getsource(redteamer)
        assert "raise KeyboardInterrupt" in src  # instant, not flag-then-wait
