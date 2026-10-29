"""output_normalizer — the model reads RESULTS, not noise.

Field evidence (2026-10-28 trail): 4 full ffuf help dumps, 15/52 steps
truncated, 54 retired-path uses. These tests pin every normalization.
"""

import json

from suijin.server.tools.lib.output_normalizer import (
    _extract_structured,
    heal_command,
    normalize_output,
)


def _fmt(stdout: str, cmd: str = "ffuf -u http://t/FUZZ") -> str:
    return f"[COMMAND] {cmd}\n[EXIT] 1 (0.1s)\n[STDOUT]\n{stdout}\n"


class TestFfufUsageDump:
    def test_usage_dump_collapses_to_diagnosis(self):
        dump = "Fuzz Faster U Fool - 2.1.0\nHTTP OPTIONS: -u URL\nMATCHER OPTIONS: -mc 200"
        out = normalize_output("ffuf -u x/FUZZ -w ~/wordlists/missing.txt", _fmt(dump))
        assert "Fuzz Faster U Fool" not in out
        assert "ARGUMENT FAILURE" in out

    def test_missing_wordlist_named(self, tmp_path):
        dump = "Fuzz Faster U Fool - 2.1.0\nHTTP OPTIONS:\nMATCHER OPTIONS:"
        out = normalize_output(f"ffuf -u x/FUZZ -w {tmp_path}/nope.txt", _fmt(dump))
        assert "wordlist not found" in out

    def test_unwritable_output_dir_named(self, tmp_path):
        dump = "Fuzz Faster U Fool\nHTTP OPTIONS:\nMATCHER OPTIONS:"
        dead = tmp_path / "no" / "such" / "dir"
        out = normalize_output(f"ffuf -u x/FUZZ -w ok.txt -o {dead}/r.json -of json", _fmt(dump))
        assert "-o directory does not exist" in out

    def test_normal_ffuf_output_kept(self):
        body = "admin [Status: 200, Words: 12, Lines: 3]\napi [Status: 200]"
        out = normalize_output("ffuf -u x/FUZZ -w w.txt", _fmt(body))
        assert "admin" in out and "ARGUMENT FAILURE" not in out


class TestFfufJsonExtraction:
    def test_results_become_a_table(self, tmp_path, monkeypatch):
        f = tmp_path / "sweep.json"
        f.write_text(
            json.dumps(
                {
                    "results": [
                        {"url": "http://t/api", "status": 200, "length": 431},
                        {"url": "http://t/admin", "status": 403, "length": 12},
                    ]
                }
            )
        )
        monkeypatch.chdir(tmp_path)
        out = normalize_output("ffuf -u t/FUZZ -w w -o sweep.json -of json", _fmt("sweeping…"))
        assert "2 result(s)" in out
        assert "http://t/api" in out and "403" in out

    def test_zero_results_is_a_clean_line(self, tmp_path, monkeypatch):
        f = tmp_path / "e.json"
        f.write_text(json.dumps({"results": []}))
        monkeypatch.chdir(tmp_path)
        out = normalize_output("ffuf -u t/FUZZ -w w -o e.json -of json", _fmt("x"))
        assert "0 results matched" in out

    def test_over_40_results_truncated_with_pointer(self, tmp_path, monkeypatch):
        f = tmp_path / "big.json"
        f.write_text(
            json.dumps({"results": [{"url": f"http://t/r{i}", "status": 200, "length": i} for i in range(55)]})
        )
        monkeypatch.chdir(tmp_path)
        out = normalize_output("ffuf -u t/FUZZ -w w -o big.json -of json", _fmt("x"))
        assert "55 result(s)" in out and "and 15 more" in out


class TestProgressStrip:
    def test_progress_lines_removed(self):
        body = ":: Progress: [1200/2000] :: Job [1/1] :: 200 req/sec ::\nadmin [Status: 200]"
        out = normalize_output("ffuf -u x/FUZZ -w w", _fmt(body))
        assert "Progress" not in out and "admin" in out


class TestStructuredExtraction:
    def test_big_json_dict_summarized(self):
        data = {"results": [{"id": i, "url": f"u{i}", "status": 200} for i in range(200)], "meta": "scan"}
        out = _extract_structured(json.dumps(data))
        assert out and "200 entries" in out and "first:" in out and "last:" in out
        assert '"id": 199' in out

    def test_big_jsonl_summarized(self):
        lines = "\n".join(json.dumps({"path": f"/p{i}", "code": 404}) for i in range(100))
        assert len(lines) > 2000  # over the extraction threshold
        out = _extract_structured(lines)
        assert out and "100" in out and "/p0" in out

    def test_small_json_untouched(self):
        small = json.dumps({"ok": True})
        assert _extract_structured(small) is None

    def test_non_json_untouched(self):
        assert _extract_structured("A" * 5000) is None


class TestPathHealing:
    def test_retired_workspace_rewritten(self):
        cmd, notes = heal_command("mkdir -p ~/.suijin/workspace/tmp/s && cd ~/.suijin/workspace/tmp/s", "/eng/home")
        assert ".suijin/workspace" not in cmd
        assert "/eng/home/tmp/s" in cmd
        assert notes and "RETIRED" in notes[0]

    def test_absolute_retired_path_rewritten(self):
        cmd, _ = heal_command("cat /Users/whoever/.suijin/workspace/tmp/s/v1.json", "/eng/home")
        assert cmd == "cat /eng/home/tmp/s/v1.json"

    def test_redirect_inside_quotes_flagged(self):
        cmd, notes = heal_command("cd '/Users/w/.suijin/engages 2>/dev/null'", "/eng/home")
        assert any("INSIDE the quoted path" in n for n in notes)

    def test_clean_command_untouched(self):
        cmd, notes = heal_command('curl -s "http://127.0.0.1:6000/api" -o "$TMPDIR/r.json"', "/eng/home")
        assert cmd == 'curl -s "http://127.0.0.1:6000/api" -o "$TMPDIR/r.json"' and not notes
