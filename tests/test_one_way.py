"""Structurally guarantee the one-way property (design.md §3.1).

The approval-voice package is voice-OUTPUT only. It must never *call* any
voice-INPUT / interactive-confirmation OpenHome API. We assert this by parsing
the package's AST and checking actual call sites — so docstrings/comments that
name the banned APIs to *document the ban* don't trip the check.

Scope note: the scan is restricted to the `approval_voice/` package and
excludes the tests directory — otherwise this file (which must spell out the
forbidden names) would match itself.
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

PACKAGE_DIR = Path(approval_voice.__file__).parent


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):  # e.g. worker.user_response()
        return func.attr
    return None


def test_package_calls_no_voice_input_api():
    offenders = []
    for py_file in PACKAGE_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called_name(node) in FORBIDDEN_APIS:
                offenders.append(f"{py_file.name}:{node.lineno} {_called_name(node)}()")
    assert not offenders, f"forbidden voice-input API call(s) found: {offenders}"
