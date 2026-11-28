"""One module object per file, whichever name it was imported through.

`suijin.modules.*` and `suijin.server.*` name the same code; the namespace unifier makes them the
same object. Until 2026-11-28 it leaked for subpackages: the aliased parent's search path points
at the server tree, so importing `suijin.modules.tools.lib` found the server's own `__init__.py`,
was taken for "a real file, not a shim", and Python built a SECOND object and executed the
server package again under the modules name. Submodules then hung off one object or the other —
the "cannot import name X" family of order-dependent failures.

Each check runs in a fresh interpreter, because import order is the whole point.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

MODULES_FIRST = ["import suijin.modules.tools.lib.job_registry", "import suijin.server.tools.lib.evidence_store"]
SERVER_FIRST = list(reversed(MODULES_FIRST))

PROBE = """
import json, sys
a = sys.modules.get("suijin.server.tools.lib")
b = sys.modules.get("suijin.modules.tools.lib")
print(json.dumps({
    "server_loaded": a is not None,
    "modules_loaded": b is not None,
    "same": a is b,
    "server_sees_what_modules_imported": hasattr(a, "job_registry"),
    "modules_sees_what_server_imported": hasattr(b, "evidence_store"),
}))
"""


def _probe(first_lines: list[str]) -> dict:
    code = "\n".join([*first_lines, PROBE])
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("order", ["modules_first", "server_first"])
def test_a_subpackage_is_one_object_whichever_name_came_first(order):
    lines = MODULES_FIRST if order == "modules_first" else SERVER_FIRST
    state = _probe(lines)
    assert state == {
        "server_loaded": True,
        "modules_loaded": True,
        "same": True,
        "server_sees_what_modules_imported": True,
        "modules_sees_what_server_imported": True,
    }


def test_the_names_a_test_file_imports_in_one_line_do_not_split_the_package():
    """The line that surfaced it — two submodules by name from the server package — followed by
    an import through the other name."""
    state = _probe(
        [
            "from suijin.server.tools.lib import evidence_store, system_stats",
            "import suijin.modules.tools.lib.job_registry",
        ]
    )
    assert state["same"] is True and state["modules_sees_what_server_imported"] is True


def test_an_aliased_module_keeps_its_own_identity_so_reload_still_works():
    """The import machinery stamps the alias spec onto the object it is handed. Left there,
    importlib.reload() resolves the alias again and executes nothing — a silent no-op."""
    code = "\n".join(
        [
            "import importlib, json, os, sys",
            "import suijin.modules.platform.lib.constants as c",
            "importlib.reload(c)",
            "os.environ['SUIJIN_TMP_DIR'] = '/custom/tmp/path'",
            "importlib.reload(c)",
            "print(json.dumps({'spec': c.__spec__.name, 'name': c.__name__, 'loader': type(c.__loader__).__name__,",
            "                  'tmp_dir': str(c.TMP_DIR), 'one_object': sys.modules['suijin.server.platform.lib.constants'] is c}))",
        ]
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
    )
    assert done.returncode == 0, done.stderr[-800:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == {
        "spec": "suijin.server.platform.lib.constants",
        "name": "suijin.server.platform.lib.constants",
        "loader": "SourceFileLoader",
        "tmp_dir": "/custom/tmp/path",  # the reload really executed the file
        "one_object": True,
    }


def _modules_only_package() -> str | None:
    """A package of the modules tree with no server twin (it has a `lib/` to import beneath it)."""
    for init in sorted((ROOT / "suijin" / "modules").glob("*/__init__.py")):
        name = init.parent.name
        if not (ROOT / "suijin" / "server" / name).exists() and (init.parent / "lib").is_dir():
            return f"suijin.modules.{name}.lib"
    return None


def test_a_package_that_exists_only_in_the_modules_tree_imports_normally():
    """importlib.util.find_spec RAISES when a parent is missing. A finder that lets that escape
    breaks every import beneath such a package — a rewrite of the finder did exactly that and
    turned about 160 passing tests red in one run."""
    target = _modules_only_package()
    if target is None:
        pytest.skip("every modules package has a server twin on this tree")
    code = "\n".join(
        ["import json, sys", f"import {target}", f"print(json.dumps([m for m in sys.modules if m == '{target}']))"]
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
    )
    assert done.returncode == 0, done.stderr[-800:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [target]


# Server packages without an __init__.py (namespace packages) have no origin at all — a third
# case the finder must still alias. (`console` is deliberately absent: suijin.modules.console is
# a real package of the modules tree, not a twin of suijin.server.console.)
NAMESPACE_PACKAGES = ["suijin.server.redteam.lib", "suijin.server.mesh"]


@pytest.mark.parametrize("package", NAMESPACE_PACKAGES)
@pytest.mark.parametrize("order", ["modules_first", "server_first"])
def test_a_namespace_package_is_one_object_too(package, order):
    twin = package.replace("suijin.server.", "suijin.modules.", 1)
    if (ROOT / package.replace(".", "/") / "__init__.py").exists():
        pytest.skip(f"{package} gained an __init__.py — covered by the regular package checks")
    code = "\n".join(
        [
            "import json, sys",
            f"import {twin}" if order == "modules_first" else f"import {package}",
            f"import {package}" if order == "modules_first" else f"import {twin}",
            f"print(json.dumps(sys.modules.get('{package}') is sys.modules.get('{twin}')))",
        ]
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
    )
    assert done.returncode == 0, done.stderr[-800:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) is True


def test_a_name_nobody_imported_through_is_simply_not_loaded():
    """Nothing is pre-registered: the modules name appears only when something asks for it, and
    then it is the server's object."""
    state = _probe(
        ["from suijin.server.tools.lib import evidence_store, system_stats", "import suijin.modules.console.lib.cli"]
    )
    assert state == {
        "server_loaded": True,
        "modules_loaded": False,
        "same": False,
        "server_sees_what_modules_imported": False,
        "modules_sees_what_server_imported": False,
    }
