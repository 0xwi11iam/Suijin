"""Submission report: platform isolation, redaction, no Suijin internals."""
from suijin.server.tools.lib.submission_report import defaults, render

REC = {
    "title": "EXP-001 CONFIRMED — HIGH CVSS 7.1 : Stored XSS in profile bio field",
    "description": "The profile bio renders unencoded input.",
    "target": "https://app.example.test",
    "severity": "high",
    "cvss": 7.1,
    "evidencePaths": ["/tmp/EXP-001/run-1.log"],
}
CATALOG = {
    "description": "A stored script executes in the profile viewer's session.",
    "poc": [{"cmd": "curl -s https://app.example.test/api/profile"}],
    "last_run": "Cookie: session=secret\n<img src=x onerror=alert(1)>",
}


def test_platform_isolation_and_no_internals():
    meta = defaults("xss", REC["title"], REC["target"])
    h1 = render(REC, meta, "hackerone", CATALOG)
    bug = render(REC, meta, "bugcrowd", CATALOG)
    inti = render(REC, meta, "intigriti", CATALOG)
    allp = render(REC, meta, "other", CATALOG)
    assert "HackerOne submission" in h1 and "Bugcrowd submission" not in h1
    assert "Bugcrowd submission" in bug and "HackerOne submission" not in bug
    assert "Intigriti submission" in inti and "HackerOne submission" not in inti
    assert "HackerOne" in allp and "Bugcrowd" in allp and "Intigriti" in allp
    assert "Description (single box)" in bug and "25,000" in bug
    for text in (h1, bug, inti, allp):
        assert "EXP-001" not in text
        assert "/Users/" not in text
        assert "audit" not in text.lower()
        assert "session=secret" not in text
    assert "Stored" in meta["vrt"]
