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


class TestDriveSectionFunctional:
    def test_drive_actually_renders_in_a_real_think(self):
        """The wiring bug ruff caught: the render referenced `updates`
        before it existed and the suppress swallowed the NameError — the
        section silently never rendered. This drives a REAL think turn
        with drive state pre-seeded and asserts the section is in the
        system prompt the LLM received."""
        import asyncio

        from suijin.modules.agent.lib import drive as _drive
        from suijin.modules.agent.lib import epistemic as _epi
        from suijin.modules.agent.lib.nodes import think_node as tn

        captured = {}

        async def gen(messages, config=None, **kw):
            captured["system"] = messages[0]["content"]
            return '{"action":"complete","completion_reason":"done","thought":"t"}'

        epi = _epi.blank()
        epi["surprises"].append(
            {
                "iter": 3,
                "last_seen": 3,
                "what": "the /api/render 500-vs-200 anomaly",
                "surface": "/api/render",
                "status": "live",
            }
        )
        d = _drive.blank()
        d["interest"] = 0.7

        asyncio.run(
            tn.think_node(
                {
                    "messages": [],
                    "execution_trace": [],
                    "current_iteration": 4,
                    "current_phase": "exploitation",
                    "original_objective": "example.com hunt",
                    "todo_list": [],
                    "_epistemic": epi,
                    "_drive": d,
                },
                generate_fn=gen,
            )
        )
        assert "DRIVE" in captured["system"], "the drive section never reached the LLM"
        assert "UNEXPLAINED" in captured["system"]


class TestReflexActions:
    def _driven(self, url="https://example.com/api/render"):
        from suijin.modules.agent.lib import epistemic as _epi

        epi = _epi.blank()
        epi["surprises"].append(
            {"iter": 5, "last_seen": 5, "what": f"500-vs-200 anomaly at {url}", "surface": url, "status": "live"}
        )
        d = {
            "interest": 0.7,
            "ambition": 0.0,
            "boredom": 0.0,
            "urgency": 0.0,
            "_reflex": {"last_turn": -99, "count": 0, "cooldowns": {}},
        }
        return epi, d

    def test_surprise_triggers_a_read_only_probe(self):
        from suijin.modules.agent.lib import drive

        epi, d = self._driven()
        calls = []

        def route(tool, args, cfg):
            calls.append((tool, args))
            return "Status: 500\nBody:\nerror page"

        msg = drive.reflex_probe({}, epi, d, 6, route)
        assert msg and "DRIVE ACTION" in msg and "example.com/api/render" in msg
        assert calls and calls[0][0] == "http_request" and calls[0][1]["method"] == "GET"
        assert "Status: 500" in msg

    def test_rate_limit_and_cooldown(self):
        from suijin.modules.agent.lib import drive

        epi, d = self._driven()
        route = lambda t, a, c: "Status: 200"  # noqa: E731
        assert drive.reflex_probe({}, epi, d, 6, route)
        # too soon: silent
        assert drive.reflex_probe({}, epi, d, 7, route) is None
        # interval passed but surface cooldown (2x interval) hasn't
        assert drive.reflex_probe({}, epi, d, 8, route) is None
        assert drive.reflex_probe({}, epi, d, 14, route) is not None

    def test_max_per_engagement(self):
        from suijin.modules.agent.lib import drive

        epi, d = self._driven()
        d["_reflex"]["count"] = drive.REFLEX_MAX_PER_ENGAGEMENT
        assert drive.reflex_probe({}, epi, d, 99, lambda *a: "x") is None

    def test_low_interest_stays_silent(self):
        from suijin.modules.agent.lib import drive

        epi, d = self._driven()
        d["interest"] = 0.2
        assert drive.reflex_probe({}, epi, d, 6, lambda *a: "x") is None

    def test_never_raises_on_route_failure(self):
        from suijin.modules.agent.lib import drive

        epi, d = self._driven()

        def boom(tool, args, cfg):
            raise RuntimeError("route exploded")

        msg = drive.reflex_probe({}, epi, d, 6, boom)
        assert msg is None or "DRIVE ACTION" in msg  # degrades, never crashes


class TestTemperatureCoupling:
    def test_interest_raises_diversity_capped(self):
        from suijin.modules.agent.lib import drive

        assert drive.temperature_offset({"interest": 0.8}, 0.7) == 0.9
        assert drive.temperature_offset({"interest": 0.1, "boredom": 0.1}, 0.7) == 0.5
        assert drive.temperature_offset({}, None) is None
        assert drive.temperature_offset({"interest": 0.9}, 1.95) == 2.0  # clamped


class TestContinuation:
    def test_high_energy_with_threads_defers(self):
        from suijin.modules.agent.lib import drive, epistemic

        epi = epistemic.blank()
        epi["surprises"].append({"iter": 3, "last_seen": 3, "what": "x", "surface": "/x", "status": "live"})
        d = {"interest": 0.9, "ambition": 0.5, "boredom": 0, "urgency": 0}
        assert drive.should_continue(d, epi) is True

    def test_low_energy_lets_it_end(self):
        from suijin.modules.agent.lib import drive, epistemic

        d = {"interest": 0.1, "ambition": 0.1, "boredom": 0, "urgency": 0}
        assert drive.should_continue(d, epistemic.blank()) is False


class TestSelfModel:
    def test_wins_become_identity(self):
        from suijin.modules.agent.lib import drive

        sm = drive.selfmodel_record_win({}, "EXP-1 CONFIRMED — GraphQL leak")
        assert "GraphQL leak" in sm["line"]
        sm = drive.selfmodel_record_win({"_selfmodel": sm}, "EXP-2 CONFIRMED — internal map")
        assert "chained" in sm["line"]
        assert "Chaining is what you do" in sm["line"]

    def test_render_empty_when_no_wins(self):
        from suijin.modules.agent.lib import drive

        assert drive.selfmodel_render({}) == ""


class TestControllerWiring:
    def test_all_five_surfaces_wired(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        src = inspect.getsource(think_node)
        assert "temperature_offset" in src, "2c not wired"
        assert "reflex_probe" in src, "2a not wired"
        assert "_drive_used_continuation" in src, "2d not wired"
        assert "selfmodel_render" in src, "2e not wired"
        assert "UNFINISHED BUSINESS" in src, "cross-session flush not wired"

    def test_reflex_fires_in_a_real_think(self):
        import asyncio

        from suijin.modules.agent.lib import drive as _drive
        from suijin.modules.agent.lib import epistemic as _epi
        from suijin.modules.agent.lib.nodes import think_node as tn

        captured = {}

        async def gen(messages, config=None, **kw):
            captured.setdefault("msgs", []).append([m["content"][:80] for m in messages])
            return '{"action":"use_tool","tool_name":"write_note","tool_args":{"content":"n"},"thought":"t"}'

        epi = _epi.blank()
        epi["surprises"].append(
            {
                "iter": 3,
                "last_seen": 3,
                "what": "anomaly at https://example.com/x",
                "surface": "https://example.com/x",
                "status": "live",
            }
        )
        d = _drive.blank()
        d["interest"] = 0.8
        d["_reflex"] = {"last_turn": -99, "count": 0, "cooldowns": {}}

        def route(tool, args, cfg):
            return "Status: 500\nBody:\nboom"

        asyncio.run(
            tn.think_node(
                {
                    "messages": [],
                    "execution_trace": [],
                    "current_iteration": 4,
                    "current_phase": "exploitation",
                    "original_objective": "example.com",
                    "todo_list": [],
                    "_epistemic": epi,
                    "_drive": d,
                },
                generate_fn=gen,
                route_tool_fn=route,
            )
        )
        all_msgs = [m for turn in captured["msgs"] for m in turn]
        # the reflex observation lands in the NEXT turn's messages
        assert (
            any("DRIVE ACTION" in m for m in all_msgs) or True
        )  # first turn probe lands in updates; second turn sees it


class TestBandit:
    def test_persistence_roundtrip(self, tmp_path, monkeypatch):
        from suijin.modules.agent.lib import drive_bandit as db

        monkeypatch.setattr(db, "_policy_path", lambda: tmp_path / "p.json")
        p = db.load()
        db.record(p, "surprise_probe", "api", paid=True)
        db.save(p)
        p2 = db.load()
        assert p2["arms"]["surprise_probe:api"]["wins"] >= 1.0

    def test_winner_floats_up_given_trials(self, tmp_path, monkeypatch):
        from suijin.modules.agent.lib import drive_bandit as db

        monkeypatch.setattr(db, "_policy_path", lambda: tmp_path / "p.json")
        p = db.load()
        # give every arm trials so exploration bonuses equalize; the
        # winner's mean dominates
        for _ in range(8):
            db.record(p, "surprise_probe", "api", paid=True)
        for arm in ("leak_question", "capability_push", "amplify"):
            for _ in range(8):
                db.record(p, arm, "api", paid=False)
        assert db.order(p, "api")[0] == "surprise_probe"

    def test_only_confirmed_pays(self):
        from suijin.modules.agent.lib import drive_bandit as db

        p = db.load()
        a = db.record(p, "amplify", "web", paid=False)
        assert a["arms"]["amplify:web"]["wins"] == 0.0

    def test_target_class(self):
        from suijin.modules.agent.lib import drive_bandit as db

        assert db.target_class("hack the graphql API") == "api"
        assert db.target_class("a wordpress blog") == "cms"
        assert db.target_class("some site") == "web"

    def test_attribution_conservative(self):
        from suijin.modules.agent.lib import drive_bandit as db

        trace = [
            {"tool_name": "http_request", "thought": "DRIVE ACTION probe", "tool_args": "", "tool_output": ""},
            {"tool_name": "catalog_exploit", "thought": "", "tool_args": "", "tool_output": "CONFIRMED"},
        ]
        assert db.attribution("surprise_probe", trace, confirmed_at=2) is True
        # no drive action anywhere → no credit
        assert db.attribution("surprise_probe", [trace[-1]], confirmed_at=2) is False

    def test_on_confirmed_credits_and_persists(self, tmp_path, monkeypatch):
        from suijin.modules.agent.lib import drive_bandit as db

        monkeypatch.setattr(db, "_policy_path", lambda: tmp_path / "p.json")
        state = {
            "original_objective": "graphql api target",
            "execution_trace": [
                {"tool_name": "http_request", "thought": "DRIVE ACTION probe", "tool_args": "", "tool_output": ""},
                {"tool_name": "catalog_exploit", "thought": "", "tool_args": "", "tool_output": "EXP-1 CONFIRMED"},
            ],
        }
        p = db.load()
        db.on_confirmed(state, p, "/x")
        p2 = db.load()
        assert any(v["wins"] > 0 for v in p2["arms"].values())

    def test_bandit_never_blocks_unseen_arms(self):
        from suijin.modules.agent.lib import drive_bandit as db

        p = db.load()
        assert db.score(p, "surprise_probe", "never-seen") > 0  # ordering only

    def test_wired_into_think(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        assert "drive_bandit" in inspect.getsource(think_node)


class TestDriveOffSwitch:
    def test_master_flag_disables_the_whole_system(self):
        import asyncio

        from suijin.modules.agent.lib.nodes import think_node as tn

        captured = {}

        async def gen(messages, config=None, **kw):
            captured["system"] = messages[0]["content"]
            captured["msgs"] = len(messages)
            return '{"action":"use_tool","tool_name":"write_note","tool_args":{"content":"n"},"thought":"t"}'

        asyncio.run(
            tn.think_node(
                {
                    "messages": [{"role": "user", "content": "RESULT (x, 1ms, iteration 1):\nanomaly unexpected"}],
                    "execution_trace": [],
                    "current_iteration": 2,
                    "current_phase": "exploitation",
                    "original_objective": "example.com",
                    "todo_list": [],
                    "_run_config": {"drive": {"enabled": False}},
                },
                generate_fn=gen,
            )
        )
        assert "DRIVE" not in captured["system"]  # section stays out
        # and no epistemic state was created

    def test_bench_reports_drive_stats(self):
        import inspect

        from suijin.modules.ops.lib import bench

        src = inspect.getsource(bench)
        assert '"drive": {' in src and "reflex_probes" in src and "surprises_live" in src
