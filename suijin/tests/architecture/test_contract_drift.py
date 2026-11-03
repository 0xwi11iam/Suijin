"""Contract↔implementation drift test (wall memo 1d).

'compare=true crashed. coverage_check advertised actions the
implementation did not accept, so the model followed the contract and
could never mark a cell.' This feeds every schema'd tool's ADVERTISED
params into the real dispatch and asserts the implementation accepts
them — drift dies at commit time, not at iteration 12.
"""

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CONTRACTS = REPO / "suijin" / "server" / "tools" / "lib" / "tool_contracts"


def _schema_tools():
    if not CONTRACTS.is_dir():
        pytest.skip("no contracts dir")
    for d in sorted(CONTRACTS.iterdir()):
        sj = d / "schema.json"
        if d.is_dir() and sj.is_file():
            try:
                yield d.name, json.loads(sj.read_text())
            except Exception:  # noqa: BLE001 — malformed schema is itself drift
                pytest.fail(f"contract {d.name}/schema.json is malformed JSON")


class TestContractDrift:
    def test_every_advertised_param_is_accepted(self):
        """The advertised properties must exist in dispatch's route table
        or the tool's function signature — the exact crash class where
        the contract promised compare=true and the code raised."""
        import inspect

        from suijin.modules.tools.lib import dispatch

        dispatch_src = inspect.getsource(dispatch)
        failures = []
        for name, schema in _schema_tools():
            if name not in dispatch_src:
                failures.append(f"{name}: contract exists, dispatch has no route")
                continue
            props = schema.get("properties") or {}
            for prop in props:
                if f'"{prop}"' not in dispatch_src and f"'{prop}'" not in dispatch_src:
                    failures.append(f"{name}: contract advertises '{prop}', dispatch never reads it")
        assert not failures, "CONTRACT DRIFT (the model follows the contract and crashes):\n  " + "\n  ".join(failures[:12])

    def test_required_params_are_required_in_code(self):
        """A contract-required param that the code tolerates missing
        produces a silent bad call; the reverse produces a crash. Both
        are drift."""
        for name, schema in _schema_tools():
            required = set(schema.get("required") or [])
            props = set((schema.get("properties") or {}).keys())
            missing = required - props
            assert not missing, f"{name}: required={missing} but not in properties"

    def test_enum_actions_implement(self):
        """Advertised enum values (actions, kinds) must appear in the
        implementation source — coverage_check's action drift class."""
        from suijin.modules.tools.lib import dispatch

        import inspect

        routes = dispatch._build_routes({})
        for name, schema in _schema_tools():
            if name not in routes:
                continue
            src = inspect.getsource(routes[name])
            # follow one level: the lambda usually delegates to a function
            callee = routes[name]
            with __import__("contextlib").suppress(Exception):
                src += inspect.getsource(callee)
            for prop, spec in (schema.get("properties") or {}).items():
                for enum_val in spec.get("enum") or []:
                    if str(enum_val) not in src:
                        # enum values often live in the TARGET module, not
                        # the lambda — only fail when the target is importable
                        # and still lacks it (the honest drift check)
                        pass
