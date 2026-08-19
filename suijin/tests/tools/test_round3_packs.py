"""Round-3 pack tools — offline behavioral tests."""

import importlib.util
from pathlib import Path
from unittest import mock

MODULES = Path(__file__).resolve().parents[3] / "suijin" / "modules"


def load_pack(name: str):
    spec = importlib.util.spec_from_file_location(f"r3.{name}", MODULES / name / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCmdSmith:
    def test_define_list_run_delete_cycle(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        m = load_pack("cmdsmith")
        assert "defined 'probe'" in m.custom_cmd_define("probe", "echo hi {name}")
        assert "probe" in m.custom_cmd_list()
        out = m.custom_cmd_run("probe", "name=world")
        assert "hi world" in out and "exit=0" in out
        assert "deleted 'probe'" in m.custom_cmd_delete("probe")
        assert "no command named" in m.custom_cmd_run("probe")

    def test_guardrail_blocks_dangerous_template(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        m = load_pack("cmdsmith")
        m.custom_cmd_define("evil", "rm -rf /")
        out = m.custom_cmd_run("evil")
        assert "denied" in out.lower()

    def test_missing_placeholder_reported(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        m = load_pack("cmdsmith")
        m.custom_cmd_define("x", "echo {a} {b}")
        assert "missing arg" in m.custom_cmd_run("x", "a=1")


class TestPyRun:
    def test_eval_expression(self):
        m = load_pack("pyrun")
        assert m.python_eval("2 + 3") == "5"
        assert m.python_eval("print('hi')") == "hi"

    def test_exec_block_and_errors(self):
        m = load_pack("pyrun")
        assert "done" in m.python_eval("for i in range(2):\n    pass\nprint('done')")
        assert m.python_eval("1/0").startswith("Error:")

    def test_script_run_missing(self):
        m = load_pack("pyrun")
        assert "not found" in m.script_run("no_such.py")


class TestHashGen:
    def test_hash_compute_text_and_hmac(self, tmp_path):
        m = load_pack("hashkit")
        out = m.hash_compute(text="abc")
        assert "ba7816bf" in out  # sha256('abc') prefix
        f = tmp_path / "f.txt"
        f.write_text("abc")
        assert "ba7816bf" in m.hash_compute(file=str(f))
        mac = m.hmac_sign(text="msg", key="k")
        assert mac.startswith("hmac-sha256 = ") and len(mac.split("= ")[1]) == 64

    def test_generators(self):
        g = load_pack("genkit")
        tok = g.random_token(length=32)
        assert len(tok) == 32 and all(c in "0123456789abcdef" for c in tok)
        pws = g.password_gen(count=2, length=20).splitlines()
        assert len(pws) == 2 and all(len(p) == 20 for p in pws)
        uuids = g.uuid_gen(count=3).splitlines()
        assert len(uuids) == 3 and all(len(u) == 36 and u.count("-") == 4 for u in uuids)


class TestNetUtils:
    def test_port_range_expand(self):
        m = load_pack("netutils")
        assert "5 ports" in m.port_range_expand("80,443,8000-8002")
        assert "bad/oversized" in m.port_range_expand("1-99999")
        assert "bad port" in m.port_range_expand("notaport")

    def test_tcp_ping_fake_socket(self, monkeypatch):
        import socket as s

        m = load_pack("netutils")

        class FakeSock:
            def close(self):
                pass

        def fake(addr, timeout=None):
            if addr[1] == 80:
                return FakeSock()
            raise OSError("refused")

        monkeypatch.setattr(s, "create_connection", fake)
        assert "1/1" in m.tcp_ping("h", 80, count=1)
        assert "0/1" in m.tcp_ping("h", 81, count=1)


class TestDnsSec:
    def test_records_and_audit(self):
        m = load_pack("dnssec")

        class FakeResp:
            status_code = 200

            def json(self):
                # domain TXT = weak SPF; _dmarc = p=none
                name = self._url.get("name", "")
                if name == "t.example":
                    ans = [{"data": '"v=spf1 include:_spf.example.com ~all"'}]
                elif name.startswith("_dmarc"):
                    ans = [{"data": '"v=DMARC1; p=none; rua=mailto:x@t.example"'}]
                else:
                    ans = []
                return {"Answer": ans}

            _url = {}

        def fake_get(url, params=None, **kw):
            r = FakeResp()
            r._url = params or {}
            return r

        with mock.patch.object(m.requests, "get", side_effect=fake_get):
            out = m.email_security_records("t.example")
            assert "SPF" in out and "p=none" in out and "spoof" not in out.splitlines()[0]
            aud = m.spf_audit("t.example")
            assert "soft fail" in aud and "includes: _spf.example.com" in aud


class TestReqTool:
    def test_raw_parse_and_curl_build(self):
        m = load_pack("reqtool")
        out = m.raw_request_parse('POST /login HTTP/1.1\nHost: t.example\nContent-Type: application/json\n\n{"u":1}')
        assert '"method": "POST"' in out and "t.example" in out
        curl = m.curl_build(method="POST", url="http://t/x", headers='{"X-A": "1"}', body="b")
        assert curl.startswith("curl") and "-X POST" in curl and "X-A: 1" in curl


class TestFileKit:
    def test_tree_grep_stat(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        (tmp_path / "outputs" / "reports").mkdir(parents=True)
        (tmp_path / "outputs" / "reports" / "r.md").write_text("secret token here")
        m = load_pack("filekit")
        assert "r.md" in m.file_tree("outputs")
        assert "secret token here" in m.file_grep("secret")
        st = m.file_stat("outputs/reports/r.md")
        assert "sha256=" in st

    def test_archive_extract_zip_slip(self, tmp_path, monkeypatch):
        import zipfile

        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        z = tmp_path / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("ok.txt", "fine")
            zf.writestr("../../escape.txt", "bad")
        m = load_pack("filekit")
        out = m.archive_extract(str(z))
        assert "blocked path traversal" in out
        assert not (tmp_path.parent / "escape.txt").exists()


class TestCronQueryPayload:
    def test_cron(self):
        m = load_pack("cronkit")
        assert "UTC" in m.cron_next("*/15 * * * *", n=1)
        assert "hourly" in m.cron_explain("30 * * * *")
        assert "5 fields" in m.cron_next("* * * *")

    def test_query(self):
        m = load_pack("querykit")
        assert '"a"' in m.json_query('{"items":[{"n":"a"},{"n":"b"}]}', "items[*].n")
        csv_out = m.csv_query("name,ok\na,1\nb,1\n", where="ok=1", sort="name")
        assert "2 rows" in csv_out
        md = m.table_markdown('[{"t":"nmap","ok":"y"}]')
        assert "| t | ok |" in md

    def test_polyglots(self):
        m = load_pack("payloadpk")
        assert "onerror" in m.xss_polyglots("html")
        assert "SLEEP" in m.sqli_polyglots("mysql")
        assert "one of" in m.sqli_polyglots("oracle")
