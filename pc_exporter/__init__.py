"""PC-side approval exporter package (Refs #7).

The PC half of the production transport (PC -> DevKit live): it reads claude-org's
live state.db READ-ONLY, maps awaiting_user gates into the section 1.3
AnnounceItem shape (shared verbatim with the on-device ability via
approval_voice/schema.py), and delivers them one-way over LAN HTTP.

This package is PC side only -- it is NOT bundled into the DevKit ability and is
deliberately excluded from the add-capability sandbox scanners (it uses sqlite3
/ raw file I/O / module-scope imports, none of which are allowed in the bundle).

Public surface:
- core.export_queue / build_queue / atomic_write_json (transport-independent)
- server.serve / make_server (one-way HTTP delivery of the exported file)
"""
from __future__ import annotations

from .core import (
    FIELDS,
    GATE_MAP,
    TITLE_BY_GATE,
    atomic_write_json,
    build_queue,
    default_out_path,
    export_queue,
    open_readonly,
    repo_root,
    resolve_db_path,
)
from .server import DEFAULT_HOST, DEFAULT_PORT, env_port, make_server, serve

__all__ = [
    "FIELDS",
    "GATE_MAP",
    "TITLE_BY_GATE",
    "atomic_write_json",
    "build_queue",
    "default_out_path",
    "export_queue",
    "open_readonly",
    "repo_root",
    "resolve_db_path",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "env_port",
    "make_server",
    "serve",
]
