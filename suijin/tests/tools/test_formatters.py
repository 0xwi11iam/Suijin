"""formatters — pretty JSON + human-channel color.

The CHANNEL RULE: pretty_json is model-safe (plain text, no ANSI);
status styling applies only at render time on human surfaces, and rich
strips color on non-terminals (piped output contains ZERO ANSI bytes).
"""

import re

from suijin.server.tools.lib.formatters import pretty_json, status_style, stylize_http_line

_ANSI = re.compile(r"\x1b\[")


class TestPrettyJson:
    def test_dict_indents(self):
        out = pretty_json({"a": 1, "b": [1, 2]})
        assert out == '{\n  "a": 1,\n  "b": [\n    1,\n    2\n  ]\n}'

    def test_json_string_indents(self):
        out = pretty_json('{"action":"plan_tools","steps":[1,2]}')
        assert '"action"' in out and "\n" in out

    def test_invalid_json_returns_raw(self):
        assert pretty_json("not json at all") == "not json at all"

    def test_never_raises(self):
        for weird in (None, 42, {"unserializable": {1, 2}}, b"bytes", object()):
            out = pretty_json(weird)  # must not raise
            assert isinstance(out, str)

    def test_model_channel_has_no_ansi(self):
        out = pretty_json({"status": 200, "body": "x" * 10})
        assert not _ANSI.search(out)

    def test_cap_respected(self):
        big = {"blob": "y" * 900}
        assert len(pretty_json(big, cap=100)) <= 100


class TestStatusStyle:
    def test_classes_map(self):
        assert status_style(200) == "green"
        assert status_style(301) == "yellow"
        assert status_style(403) == "bright_red"
        assert status_style(404) == "red"
        assert status_style(500) == "magenta"

    def test_status_line_detected_both_shapes(self):
        assert stylize_http_line("Status: 200 OK")[1] == "green"
        assert stylize_http_line("HTTP/1.1 403 Forbidden")[1] == "bright_red"

    def test_result_prefixes_quiet(self):
        assert stylize_http_line("[COMMAND] curl -s x")[1] == "cyan"
        assert stylize_http_line("[STDOUT]")[1] == "dim"
        assert stylize_http_line("[EXIT] -1 (30s)")[1] == "bright_red"
        assert stylize_http_line("[EXIT] 0 (0.1s)")[1] == "dim"

    def test_prose_untouched(self):
        assert stylize_http_line("just some body text")[1] == ""


class TestPipedOutputIsClean:
    def test_cli_debug_pipes_without_ansi(self, tmp_path, monkeypatch, capsys):
        """The operator reads piped output in logs — ZERO ANSI bytes when
        stdout is not a terminal (rich's built-in capability check)."""
        import types

        from suijin.modules.console.lib import cli

        eng = tmp_path / "eng"
        (eng / "toollogs").mkdir(parents=True)
        (eng / "toollogs" / "job_output_1.txt").write_text(
            ":: Progress: [1/2] ::\nStatus: 200\nStatus: 403\nhttp://t.example/admin\n"
        )
        args = types.SimpleNamespace(debug_action="job", path=str(eng), extract_only=False)
        rc = cli.run_debug(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "Status: 200" in out  # pytest captures are non-tty → plain
        assert not _ANSI.search(out), "ANSI leaked into piped output"
