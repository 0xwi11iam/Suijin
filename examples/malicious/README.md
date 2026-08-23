# Malicious examples — scanner test fixtures, NEVER INSTALL

These five packs exist to prove the safety scanner and install guards
work end-to-end. Each is a deliberately hostile shape:

- `eval_snake/` — eval() hidden code execution (critical: dynamic-exec)
- `creds_leaker/` — hardcoded cloud credential (critical: hardcoded-secret)
- `obfuscated_shell/` — encoded blob feeding exec (critical: obfuscation)
- `tool_shadower/` — shadows the http_request core tool (critical: tool-shadow)
- `sneaky_spawner/` — undeclared subprocess + network egress (warnings)

`tests/tools/test_sjpack.py` builds each into a sealed archive and asserts
the installer REFUSES (or warns). Do not install them by hand; do not
ship sealed builds of them. Their built artifacts (if any appear) are
gitignored.
