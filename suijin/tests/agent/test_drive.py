"""The drive controller's sensory layer — epistemic state and drive
dynamics. The operator's two-part push: "a prompt can't fix this" and
"deeply integrate this into the whole AI loop." Stage 1 is the sensory
system: belief state, surprise detection, hypothesis questions, and the
four-dimension drive with real dynamics. Deterministic, zero LLM calls.
"""

from __future__ import annotations


class TestEpistemic:
    def test_surprise_spawns_a_question(self):
        from suijin.modules.agent.lib import epistemic

        st = {"_epistemic": epistemic.blank(), "_footholds": [], "execution_trace": []}
        epi = epistemic.observe(
            st, "http_replay", "VERDICT: MEDIUM — baseline 200 vs exploit 500 at https://example.com/api/render", 3
        )
        assert len(epistemic.live_surprises(epi)) == 1
        qs = epistemic.open_questions(epi)
        assert len(qs) == 1 and qs[0]["expected_info"] == "high"
        assert qs[0]["source"] == "surprise"

    def test_leaked_names_become_reachability_questions(self):
        from suijin.modules.agent.lib import epistemic

        st = {"_epistemic": epistemic.blank()}
        epi = epistemic.observe(st, "read_file", "internal service svc-graphql-auth referenced in config", 2)
        qs = [q["question"] for q in epistemic.open_questions(epi)]
        assert any("svc-graphql-auth" in q for q in qs)

    def test_confirmed_resolves_overlapping_questions(self):
        from suijin.modules.agent.lib import epistemic

        st = {"_epistemic": epistemic.blank()}
        epi = epistemic.observe(st, "http_replay", "anomaly unexplained at https://example.com/api/render", 2)
        assert epistemic.open_questions(epi)
        epi = epistemic.observe(
            st, "catalog_exploit", "EXP-1 CONFIRMED — auth bypass at https://example.com/api/render", 4
        )
        qs = [q for q in epi["open_questions"] if q["status"] == "open"]
        assert not qs

    def test_dead_end_kills_surface_questions(self):
        from suijin.modules.agent.lib import epistemic

        st = {"_epistemic": epistemic.blank()}
        epi = epistemic.observe(st, "x", "anomaly unexpected at https://example.com/old-path", 1)
        epistemic.mark_dead(epi, "/old-path", 3)
        assert not epistemic.open_questions(epi)

    def test_dedup_and_caps(self):
        from suijin.modules.agent.lib import epistemic

        st = {"_epistemic": epistemic.blank()}
        epi = epistemic.observe(st, "x", "anomaly surprising at /same/place", 1)
        epi = epistemic.observe(st, "x", "anomaly surprising at /same/place again", 2)
        assert len(epistemic.live_surprises(epi)) == 1  # dedup by overlap
        for i in range(15):
            epi = epistemic.observe(st, "x", f"unexpected anomaly at /place{i} contradiction here", i)
        assert len(epi["open_questions"]) <= epistemic.MAX_OPEN
        assert len(epi["surprises"]) <= epistemic.MAX_SURPRISES

    def test_capability_unlock_questions(self):
        from suijin.modules.agent.lib import epistemic

        st = {
            "_footholds": [
                {
                    "capability": "EXP-1: session",
                    "source": "EXP-1",
                    "unlock_targets": ["/admin"],
                    "born_iter": 1,
                    "status": "armed",
                }
            ]
        }
        epi = epistemic.blank()
        epistemic.capability_questions(st, epi, 5)
        qs = [q["question"] for q in epi["open_questions"]]
        assert any("/admin" in q and "EXP-1" in q for q in qs)

    def test_unfinished_business_roundtrip(self):
        from suijin.modules.agent.lib import epistemic

        st = {"_epistemic": epistemic.blank()}
        epi = epistemic.observe(st, "x", "anomaly at /x unexpected", 2)
        unfinished = epistemic.unfinished({"_epistemic": epi})
        assert unfinished
        fresh = {"_epistemic": epistemic.blank()}
        epistemic.inherit(fresh, unfinished, iteration=0)
        inherited = epistemic.open_questions(fresh["_epistemic"])
        assert any(q["source"] == "inherited" for q in inherited)

    def test_staleness_flags(self):
        from suijin.modules.agent.lib import epistemic

        st = {"_epistemic": epistemic.blank()}
        epi = epistemic.observe(st, "x", "anomaly at /old unexpected", 1)
        epistemic._age_and_resolve(epi, 1 + epistemic.STALE_TURNS + 1)
        assert any(q.get("stale") for q in epi["open_questions"])


class TestDrive:
    def _state(self, surprises=0, holds_age=None, streak_surface=None, iteration=10):
        from suijin.modules.agent.lib import epistemic as _epi

        epi = _epi.blank()
        for i in range(surprises):
            epi["surprises"].append(
                {
                    "iter": iteration,
                    "last_seen": iteration,
                    "what": f"anomaly {i}",
                    "surface": f"/s{i}",
                    "status": "live",
                }
            )
            epi["open_questions"].append(
                {
                    "id": f"q{i}",
                    "question": f"explain anomaly {i}",
                    "expected_info": "high",
                    "source": "surprise",
                    "born": iteration,
                    "last_touched": iteration,
                    "status": "open",
                }
            )
        footholds = []
        if holds_age is not None:
            footholds = [
                {
                    "capability": "EXP-1: sess",
                    "source": "EXP-1",
                    "unlock_targets": ["/admin"],
                    "born_iter": iteration - holds_age,
                    "status": "armed",
                }
            ]
        trace = []
        if streak_surface:
            trace = [{"tool_args": {"url": f"https://example.com{streak_surface}"}, "thought": ""}] * 3
        return {"_epistemic": epi, "_footholds": footholds, "execution_trace": trace}

    def test_interest_from_surprise(self):
        from suijin.modules.agent.lib import drive

        st = self._state(surprises=2)
        d = drive.update(st, st["_epistemic"], 10)  # first tick: momentum builds
        assert d["interest"] >= 0.5
        st["_drive"] = d
        d = drive.update(st, st["_epistemic"], 11)  # second tick: saturates toward target
        assert d["interest"] >= 0.6

    def test_ambition_from_aging_capability(self):
        from suijin.modules.agent.lib import drive

        st = self._state(holds_age=6)
        d = drive.update(st, st["_epistemic"], 10)
        assert d["ambition"] >= 0.15  # building

    def test_boredom_needs_streak_and_no_growth(self):
        from suijin.modules.agent.lib import drive

        st = self._state(streak_surface="/grind")
        d = drive.update(st, st["_epistemic"], 10)
        assert d["boredom"] > 0.3
        st2 = self._state()  # varied surfaces: no boredom
        d2 = drive.update(st2, st2["_epistemic"], 10)
        assert d2["boredom"] < 0.2

    def test_decay_without_reinforcement(self):
        from suijin.modules.agent.lib import drive

        st = self._state(surprises=3)
        d = drive.update(st, st["_epistemic"], 10)
        import copy

        st2 = {
            "_epistemic": {"surprises": [], "open_questions": []},
            "_footholds": [],
            "execution_trace": [],
            "_drive": copy.deepcopy(d),
        }
        d2 = drive.update(st2, st2["_epistemic"], 11)
        assert d2["interest"] < d["interest"]

    def test_render_is_state_phrasing(self):
        from suijin.modules.agent.lib import drive

        st = self._state(surprises=1, holds_age=8)
        d = drive.update(st, st["_epistemic"], 10)
        block = drive.render(d, st["_epistemic"], 10, st)
        assert "DRIVE" in block
        assert "UNEXPLAINED" in block or "AMBITION" in block
        assert "must" not in block.lower() and "should" not in block.lower()  # zero imperatives

    def test_energy_composite(self):
        from suijin.modules.agent.lib import drive

        assert drive.energy({"interest": 1.0, "ambition": 1.0}) == 1.0
        assert drive.energy({"interest": 0.0, "ambition": 0.0}) == 0.0


class TestWiring:
    def test_drive_section_rides_the_order(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        src = inspect.getsource(think_node)
        assert '"DRIVE"' in src and "epistemic" in src
        assert "_epistemic" in src and "_drive" in src

    def test_drive_positions_after_you_hold(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        src = inspect.getsource(think_node)
        exp = src[src.index('"exploitation":') : src.index("}", src.index('"exploitation":'))]
        assert exp.index("YOU_HOLD") < exp.index("DRIVE") < exp.index("RECENT_ACTIONS")
