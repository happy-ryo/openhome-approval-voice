"""Pin OpenHome add-capability sandbox compliance for the deployable bundle.

The on-device ability (`openhome_ability/`) plus the bundled pure logic
(`approval_voice/`) must pass OpenHome's add-capability static scan (design.md
§M3.1) — that scan is what previously rejected the raw-`open()` / `os` / wrong
category bundle. `deploy/sandbox_lint.py` reproduces those forbidden patterns;
this test fails the suite the moment one (os, sys, module-scope json, raw open,
signal, print, assert, asyncio.sleep, pickle, ...) creeps into a bundled file —
so we catch it here, not at upload time on a live account.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
# sandbox_lint lives in deploy/ (a dev tool, deliberately not part of the bundle).
sys.path.insert(0, str(_REPO_ROOT / "deploy"))

from sandbox_lint import scan_paths, scan_text  # noqa: E402

# The exact set of files that get zipped into the capability (see build_zip.py).
BUNDLED_DIRS = [
    str(_REPO_ROOT / "openhome_ability"),
    str(_REPO_ROOT / "approval_voice"),
]


def test_bundled_files_are_sandbox_compliant():
    violations = scan_paths(BUNDLED_DIRS, root=str(_REPO_ROOT))
    assert not violations, "sandbox violations in bundle:\n  " + "\n  ".join(violations)


def test_linter_actually_flags_known_violations():
    # Guard the guard: a silently broken scanner would pass everything. Prove it
    # flags the patterns that matter, and that method-local json stays allowed.
    bad = "import os\n\nasync def f():\n    return open('x')\n"
    found = scan_text(bad, "bad.py")
    assert any("import os" in v for v in found)
    assert any("open(" in v for v in found)

    assert any(
        "module-scope import json" in v for v in scan_text("import json\n", "j.py")
    )

    method_local = "def f():\n    import json\n    return json.loads('[]')\n"
    assert scan_text(method_local, "ok.py") == []  # method-local json is allowed


def test_linter_flags_dunder_attribute_access():
    # The server-side add-capability scan rejects dunder attribute access as a
    # suspicious introspection escape (this regression was a real upload failure:
    # cls.__dataclass_fields__). The linter must catch the access form...
    assert any(
        "dunder attribute access" in v for v in scan_text("x = cls.__dataclass_fields__\n", "d.py")
    )
    assert any("dunder attribute access" in v for v in scan_text("y = o.__class__\n", "d.py"))
    assert any("dunder access" in v for v in scan_text("getattr(o, '__globals__')\n", "g.py"))

    # ...but NOT dunder method definitions / __future__ import / __all__ assignment,
    # which are not attribute access and are used by shipped abilities.
    allowed = (
        "from __future__ import annotations\n"
        "__all__ = ['C']\n"
        "class C:\n"
        "    def __init__(self):\n"
        "        self.value = 1\n"
        "    def __post_init__(self):\n"
        "        return self.value\n"
    )
    assert scan_text(allowed, "ok.py") == []


def test_linter_flags_forbidden_module_imports():
    # The server rejects debug/introspection module imports ("Forbidden module
    # import: trace" was a real upload failure from a diagnostic `import traceback`).
    # The denylist must catch both import forms, at module AND method scope.
    assert any("forbidden module import" in v for v in scan_text("import traceback\n", "t.py"))
    assert any(
        "forbidden module import" in v
        for v in scan_text("from traceback import format_exc\n", "t.py")
    )
    assert any(
        "forbidden module import" in v
        for v in scan_text("def f():\n    import traceback\n    return traceback\n", "t.py")
    )
    for mod in ("trace", "pdb", "ctypes", "inspect", "pickle"):
        assert any(
            "forbidden module import" in v for v in scan_text(f"import {mod}\n", "t.py")
        ), mod

    # method-local json and the framework/stdlib the bundle actually uses stay OK.
    ok = (
        "from src.agent.capability import MatchingCapability\n"
        "from collections.abc import Iterable\n"
        "import logging\n"
        "def f():\n    import json\n    return json.loads('[]')\n"
    )
    assert scan_text(ok, "ok.py") == []
