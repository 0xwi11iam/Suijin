"""shell — the Textual UI shell: pages, dragon persistence, hand-off."""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.platform.lib import blockfont  # noqa: E402
from suijin.modules.platform.lib.banner import styled_lines  # noqa: E402


class TestBlockFont:
    def test_font_covers_all_headers(self):
        for w in ("INITIALIZING", "MODE SELECTOR", "RED TEAM", "BLUE TEAM", "SETTINGS",
                  "OPERATOR TOOLS", "EXIT", "OBJECTIVE", "UPLOAD FILE", "LAUNCH",
                  "CODEBASE", "BUILT-IN LABS", "v6.6.1", "EDIT"):
            assert blockfont.font_covers(w), w

    def test_render_rows(self):
        rows = blockfont.render("RED", style_of=lambda ch, i: "red")
        assert len(rows) == 5

    def test_unknown_glyph_blank(self):
        assert blockfont.glyph_rows("¿") is None
        rows = blockfont.render("¿")
        assert all(r.plain.strip() == "" for r in rows)


class TestDragonStyles:
    def test_red_bands_present(self):
        segs = styled_lines()
        styles = {s for row in segs for _, s in row}
        assert {"cyan", "red"} <= styles

    def test_eye_white(self):
        segs = styled_lines()
        assert any(s == "eye" for row in segs for _, s in row)

    def test_band_pattern(self):
        # rows 4,5,6 of every 7 are red (3-row bands, 4 cyan between)
        segs = styled_lines()
        for idx, row in enumerate(segs):
            if not row:
                continue
            base = next(s for _, s in row if s != "eye")
            assert base == ("red" if idx % 7 >= 4 else "cyan"), f"row {idx}: {base}"


class TestShellFlow:
    @pytest.fixture()
    def app(self):
        from suijin.modules.console.lib.shell import ShellApp

        return ShellApp()

    def test_welcome_shows_version_and_start(self, app):
        async def go():
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                app.screen.query_one("#start")
                app.screen.query_one("#versionart")
                return True

        assert asyncio.run(go())

    def test_full_red_flow_returns_result(self, app):
        async def go():
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                await pilot.press("enter")  # welcome → boot
                await pilot.pause(3.5)  # boot dwell (random 1-3s)
                app.screen.query_one("#mode-red")
                await pilot.click("#mode-red")
                await pilot.pause()
                await pilot.click("#red-type")
                await pilot.pause()
                await pilot.click("#obj")
                await pilot.press(*"objective from test")
                await pilot.click("#continue")
                await pilot.pause()
                await pilot.click("#launch")
                await pilot.pause(0.5)
                return app.result

        result = asyncio.run(go())
        assert result is not None and result.kind == "red"
        assert result.objective == "objective from test"

    def test_blue_lab_flow(self, app):
        async def go():
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                await pilot.press("enter")
                await pilot.pause(3.5)
                await pilot.click("#mode-blue")
                await pilot.pause()
                await pilot.click("#blue-lab")
                await pilot.pause()
                await pilot.click("#lab-hill_ctf")
                await pilot.pause(0.5)
                return app.result

        result = asyncio.run(go())
        assert result is not None and result.kind == "blue_lab"
        assert result.lab == "hill_ctf"

    def test_esc_back_navigates(self, app):
        async def go():
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                await pilot.press("enter")
                await pilot.pause(3.5)
                await pilot.click("#mode-red")
                await pilot.pause()
                await pilot.press("escape")  # red entry → selector
                await pilot.pause(0.3)
                return type(app.screen).__name__

        assert asyncio.run(go()) in ("SelectorScreen", "ShellPage")

    def test_dragon_on_current_page(self, app):
        from suijin.modules.console.lib.shell import DragonWidget

        async def go():
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                assert app.screen.query(DragonWidget)
                await pilot.press("enter")
                await pilot.pause(3.5)
                assert app.screen.query(DragonWidget)  # boot page composes one too
                await pilot.click("#mode-red")
                await pilot.pause()
                assert app.screen.query(DragonWidget)  # red entry (ShellPage layout)
                return True

        assert asyncio.run(go())

    def test_empty_objective_inline_error(self, app):
        async def go():
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                await pilot.press("enter")
                await pilot.pause(3.5)
                await pilot.click("#mode-red")
                await pilot.pause()
                await pilot.click("#red-type")
                await pilot.pause()
                await pilot.click("#continue")  # empty objective
                await pilot.pause(0.3)
                # still on the SAME page (no crash, no navigation)
                return type(app.screen).__name__

        assert asyncio.run(go()) == "ObjectiveInputScreen"
