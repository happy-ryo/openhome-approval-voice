"""Structurally guarantee the one-way property (design.md §3.1).

The approval-voice package is voice-OUTPUT only. It must never *call* any
voice-INPUT / interactive-confirmation OpenHome API. We assert this by parsing
the package's AST and checking actual call sites — so docstrings/comments that
name the banned APIs to *document the ban* don't trip the check.

Scope note: the scan covers the `approval_voice/` package AND the deployable
on-device ability (`openhome_ability/`, the M3 real-OpenHome path), and excludes
the tests directory — otherwise this file (which must spell out the forbidden
names) would match itself.

M3: the forbidden set is the *complete* input/capture surface confirmed against
a real shipped ability (openhome-dev/abilities · alarm-timer): the only
input-capture methods are `user_response()` and `run_io_loop()` (the latter
"speaks then waits for a reply" — banned even though it speaks). Both are listed
below, so the guard is exhaustive ("網羅") for the on-device path too.
"""

import ast
from pathlib import Path

import approval_voice

# APIs that would introduce a voice return-path. Banned (design.md §3.1).
FORBIDDEN_APIS = {
    "user_response",
    "run_io_loop",
    "run_confirmation_loop",
    "start_audio_recording",
}

_REPO_ROOT = Path(__file__).parent.parent
# Both the pure package and the deployable on-device ability must stay one-way.
SCAN_DIRS = [
    Path(approval_voice.__file__).parent,
    _REPO_ROOT / "openhome_ability",
]


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):  # e.g. worker.user_response()
        return func.attr
    return None


def test_package_calls_no_voice_input_api():
    offenders = []
    scanned = 0
    for scan_dir in SCAN_DIRS:
        for py_file in scan_dir.rglob("*.py"):
            scanned += 1
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _called_name(node) in FORBIDDEN_APIS:
                    offenders.append(
                        f"{py_file.parent.name}/{py_file.name}:{node.lineno} "
                        f"{_called_name(node)}()"
                    )
    # Guard against the scan silently covering nothing (e.g. a moved folder).
    assert scanned > 0, "one-way scan found no files — check SCAN_DIRS paths"
    assert not offenders, f"forbidden voice-input API call(s) found: {offenders}"
