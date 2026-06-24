#!/usr/bin/env python3
"""Static sandbox-compliance lint for the deployable OpenHome ability bundle.

OpenHome's `add-capability` rejects an uploaded capability whose code uses a set
of forbidden modules / patterns (docs/design.md §M3.1). Because we cannot run the
real add-capability scan from CI, this module reproduces those rules locally so
`deploy/build_zip.py` can refuse to ship a non-compliant zip and
`tests/test_sandbox_compliance.py` can pin compliance as a unit test.

Rule sources (grounded, design.md §M3.1):
- OpenHome SDK reference "sandbox rules": low-level platform access (`os`),
  module-scope data-encoding import (`json` at top level), low-level signal
  module, raw file open, plus `pickle`/`exec`/`eval` and platform internals
  (`redis` / `user_config` / `connection_manager`).
- openhome-dev/abilities `validate_ability.py` (the repo PR validator) regex set:
  raw `open(`, `print(`, `assert`, `asyncio.sleep(`/`asyncio.create_task(`,
  `exec(`/`eval(`, `pickle.`/`dill.`/`shelve.`/`marshal.`, `hashlib.md5(`.

We enforce the UNION so the bundle passes either scanner. The scan is run over
raw source (comments + docstrings included) on purpose: the SDK reference notes
the low-level signal rule applies "even in docstrings/comments", so the safe
posture is to keep every forbidden literal out of the bundled files entirely -
the detailed rationale lives in non-bundled docs (design.md / DEPLOY.md).

`json` is special: it is allowed *inside method bodies* (the ability imports it
locally) but forbidden at module scope. That distinction needs structure, so the
top-level `json` check is AST-based; everything else is regex over raw text.
"""
from __future__ import annotations

import ast
import os
import re

# (compiled regex, human message). Applied to raw source, comments included.
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?m)^\s*import\s+os\b"), "import os (low-level platform access)"),
    (re.compile(r"(?m)^\s*from\s+os\b"), "from os import (low-level platform access)"),
    (re.compile(r"\bos\.[A-Za-z_]"), "os.* usage (low-level platform access)"),
    (re.compile(r"(?m)^\s*import\s+sys\b"), "import sys (low-level platform access)"),
    (re.compile(r"(?m)^\s*from\s+sys\b"), "from sys import (low-level platform access)"),
    (re.compile(r"\bsys\.[A-Za-z_]"), "sys.* usage (incl. path rewriting)"),
    (re.compile(r"(?m)^\s*import\s+signal\b"), "import signal (low-level signal module)"),
    (re.compile(r"(?m)^\s*from\s+signal\b"), "from signal import (low-level signal module)"),
    (re.compile(r"\bsignal\.[A-Za-z_]"), "signal.* usage (low-level signal module)"),
    (re.compile(r"\bimport\s+redis\b"), "import redis (platform internal)"),
    (re.compile(r"\bimport\s+user_config\b"), "import user_config (platform internal)"),
    (re.compile(r"\bconnection_manager\b"), "connection_manager (platform internal)"),
    (re.compile(r"\bopen\s*\("), "raw open( - use capability_worker file helpers"),
    (re.compile(r"\bprint\s*\("), "print( - use editor_logging_handler"),
    (re.compile(r"(?m)^\s*assert\s+"), "assert - use explicit error handling"),
    (re.compile(r"\basyncio\.sleep\s*\("), "asyncio.sleep( - use session_tasks.sleep()"),
    (re.compile(r"\basyncio\.create_task\s*\("), "asyncio.create_task( - use session_tasks.create()"),
    (re.compile(r"\bexec\s*\("), "exec( - not allowed"),
    (re.compile(r"\beval\s*\("), "eval( - not allowed"),
    (re.compile(r"\bpickle\."), "pickle. - not allowed"),
    (re.compile(r"\bdill\."), "dill. - not allowed"),
    (re.compile(r"\bshelve\."), "shelve. - not allowed"),
    (re.compile(r"\bmarshal\."), "marshal. - not allowed"),
    (re.compile(r"\bhashlib\.md5\s*\("), "hashlib.md5( - weak hash, not allowed"),
]


def _lineno(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _toplevel_json_violations(text: str, rel: str) -> list[str]:
    """Flag module-scope `import json` / `from json` (method-local json is OK)."""
    out: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [f"{rel}:{e.lineno}: syntax error ({e.msg})"]
    for node in tree.body:  # module top-level statements only
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "json":
                    out.append(f"{rel}:{node.lineno}: module-scope import json "
                               "(import json inside a method body instead)")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "json":
                out.append(f"{rel}:{node.lineno}: module-scope from json import "
                           "(import json inside a method body instead)")
    return out


def scan_text(text: str, rel: str) -> list[str]:
    """Return violation strings ("rel:line: message") for one source string."""
    violations: list[str] = []
    for pattern, message in _RULES:
        for m in pattern.finditer(text):
            violations.append(f"{rel}:{_lineno(text, m.start())}: {message}")
    violations.extend(_toplevel_json_violations(text, rel))
    return violations


def scan_file(path: str, rel: str | None = None) -> list[str]:
    with open(path, encoding="utf-8") as f:  # dev tool, not part of the bundle
        text = f.read()
    return scan_text(text, rel or os.path.basename(path))


def scan_paths(paths: list[str], root: str | None = None) -> list[str]:
    """Scan every *.py under each path (file or directory). Sorted violations."""
    violations: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for dirpath, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    if f.endswith(".py"):
                        full = os.path.join(dirpath, f)
                        rel = os.path.relpath(full, root) if root else full
                        violations.extend(scan_file(full, rel))
        elif p.endswith(".py"):
            rel = os.path.relpath(p, root) if root else p
            violations.extend(scan_file(p, rel))
    return sorted(violations)
