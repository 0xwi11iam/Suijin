"""search_cve version relevance — the nginx-1.18 catch (2026-10-29).

The old exact-version boost was a bare substring over str(list): "1.1.18"
contains "1.18", so a 2012 CVE for 1.0.x-1.1.x outranked everything.
Boundary-aware now, with an explicit applicability note on mismatches.
"""

import pytest


def _nvd_payload():
    def cve(cid, desc, score):
        return {
            "cve": {
                "id": cid,
                "descriptions": [{"lang": "en", "value": desc}],
                "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": score, "baseSeverity": "HIGH"}}]},
                "references": [],
                "weaknesses": [],
            }
        }

    return {
        "vulnerabilities": [
            cve("CVE-2012-0001", "affects nginx 1.1.3 through 1.1.18 badly", 9.0),
            cve("CVE-2020-1234", "affects nginx 1.18 and 1.19 deployments", 7.5),
            cve("CVE-2009-0002", "ancient nginx 0.5.x issue", 7.2),
        ],
        "totalResults": 3,
    }


@pytest.fixture
def _mock_nvd(monkeypatch):
    import suijin.server.tools.lib.intel as intel

    class R:
        status_code = 200

        def json(self):
            return _nvd_payload()

    monkeypatch.setattr(intel.requests, "get", lambda *a, **k: R())
    import suijin.modules.knowledge.lib.cve_mirror as mirror

    monkeypatch.setattr(mirror, "search_kev", lambda *a, **k: [])
    monkeypatch.setattr(intel, "_is_kev", lambda c: False)


def _search(monkeypatch, tmp_path, version="1.18"):
    monkeypatch.delenv("NVD_API_KEY", raising=False)
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")
    from suijin.server.tools.lib.intel import search_cve

    return search_cve("nginx", {}, version=version, limit=3)


class TestVersionRelevance:
    def test_boundary_match_outranks_substring_trap(self, _mock_nvd, monkeypatch, tmp_path):
        out = _search(monkeypatch, tmp_path)
        first_id = out[out.index("[CVE-") : out.index("]", out.index("[CVE-")) + 1]
        assert first_id == "[CVE-2020-1234]", "true 1.18 advisory must rank first"
        assert "1.1.18" not in out.split("\n")[2]  # the substring-trap CVE is not the headline

    def test_mismatch_carries_applicability_note(self, _mock_nvd, monkeypatch, tmp_path):
        out = _search(monkeypatch, tmp_path)
        assert "version note: advisory text does not mention 1.18" in out

    def test_exact_match_has_no_note(self, _mock_nvd, monkeypatch, tmp_path):
        out = _search(monkeypatch, tmp_path)
        line_2020 = [ln for ln in out.splitlines() if "CVE-2020-1234" in ln][0]
        assert "version note" not in line_2020

    def test_no_version_skips_notes(self, _mock_nvd, monkeypatch, tmp_path):
        out = _search(monkeypatch, tmp_path, version=None)
        assert "version note" not in out
