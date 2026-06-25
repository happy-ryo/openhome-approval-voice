"""Structurally guarantee the on-device ability has no write-back path (design.md §3.1 / §M3.3.1).

The production transport is a PC-side push: the ability is a storage-only reader
and makes NO network call at all (the earlier outbound GET was removed because
`urllib` is sandbox-denylisted). This guard pins that invariant against
regression — should anyone re-add an outbound channel (a requests-based pull, a
raw `urlopen`, etc.), it must still only ever **GET** (receive) and never send a
body back to the PC. `urllib.request.urlopen(url)` / `Request(url)` are GETs; the
moment either carries a `data=` argument they become POSTs, and any
`.post(`/`.put(`/`.patch(` call is a write-back. We AST-scan the deployable
ability so a return channel can never creep in — the network sibling of
`tests/test_one_way.py`. (Today the bundle has zero such calls, so this passes
vacuously; it earns its keep the moment an outbound transport is reintroduced.)
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
# Both deployed surfaces: the on-device ability AND the pure package bundled
# beside it in the zip (build_zip.py nests approval_voice/ under the wrap folder).
# A write-back added in either ships, so both must be scanned.
_SCAN_DIRS = (_REPO_ROOT / "openhome_ability", _REPO_ROOT / "approval_voice")

# Attribute calls that are HTTP write verbs (requests/httpx style). `delete` is
# intentionally excluded: the capability_worker file API uses `delete_file`, and a
# bare `.delete(` is not part of any transport we ship.
_WRITE_VERBS = {"post", "put", "patch"}
# Callables that GET by default but POST when handed `data=`.
_GET_CALLABLES = {"urlopen", "Request"}


def _attr_or_name(call: ast.Call):
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _has_data_arg(node: ast.Call) -> bool:
    """True if a urlopen/Request call supplies a `data` body (GET -> POST).

    `data` is the 2nd POSITIONAL parameter of both `urlopen(url, data=None, ...)`
    and `Request(url, data=None, ...)`, so a positional `urlopen(url, body)` is a
    POST too — we must flag a 2nd positional arg, not just the `data=` keyword.
    """
    return len(node.args) > 1 or any(kw.arg == "data" for kw in node.keywords)


def test_ability_makes_no_outbound_write_calls():
    offenders = []
    scanned = 0
    for scan_dir in _SCAN_DIRS:
        for py_file in scan_dir.rglob("*.py"):
            scanned += 1
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _attr_or_name(node)
                if name in _WRITE_VERBS:
                    offenders.append(f"{py_file.name}:{node.lineno} {name}() write verb")
                if name in _GET_CALLABLES and _has_data_arg(node):
                    offenders.append(
                        f"{py_file.name}:{node.lineno} {name}(..data..) — GET turned POST"
                    )
    assert scanned > 0, "outbound scan found no files — check _SCAN_DIRS"
    assert not offenders, f"outbound write-back call(s) found: {offenders}"


def test_guard_catches_positional_and_keyword_data():
    # Prove the guard flags BOTH a positional and a keyword data body (so a
    # regression that turns the GET into a POST cannot pass silently).
    for src in ("urlopen(u, body)", "urlopen(u, data=body)",
                "Request(u, body)", "Request(u, data=body)"):
        tree = ast.parse(src)
        call = tree.body[0].value
        assert _has_data_arg(call), src
    # ...and does NOT flag the shipped GET shape.
    for src in ("urlopen(u)", "urlopen(u, timeout=5)"):
        call = ast.parse(src).body[0].value
        assert not _has_data_arg(call), src
