"""Self-contained state.db seed helpers for the pc_exporter tests (Refs #7).

The salvaged base seeded via claude-org's ``tools.state_db`` (apply_schema /
connect). This repo (openhome-approval-voice) does NOT vendor that package, so
the tests build a minimal ``events`` table directly with stdlib sqlite3. Only
the columns the exporter actually reads are needed: the exporter selects
``id, occurred_at, payload_json`` and filters on ``kind``.

``seed_state_db`` seeds in WAL mode and checkpoints (TRUNCATE) before returning,
folding the WAL into the main .db file -- deterministic main-db bytes for a
stable read-only-does-not-mutate sha256 assertion. (Note: closing the last
connection also folds + removes the WAL, so a seeded-then-closed db never leaves
an active ``-wal``; exercising a read against a NON-empty WAL requires a writer
connection held open through the read, which the relevant test does inline.)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_CREATE = (
    "CREATE TABLE IF NOT EXISTS events ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " occurred_at TEXT NOT NULL,"
    " actor TEXT,"
    " kind TEXT NOT NULL,"
    " payload_json TEXT"
    ")"
)


def seed_state_db(db_path: Path, events: list[dict]) -> None:
    """Seed a real WAL-mode state.db with raw event rows.

    ``events`` items: {kind, occurred_at, payload(dict|list|str|None), actor?}.
    A dict/list payload is JSON-encoded; ``None`` becomes ``"{}"``; a raw string
    is stored verbatim (used to exercise the malformed-payload tolerant decode).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(_CREATE)
        for ev in events:
            payload = ev.get("payload")
            if isinstance(payload, (dict, list)):
                payload_json = json.dumps(payload)
            elif payload is None:
                payload_json = "{}"
            else:
                payload_json = payload  # raw string (e.g. malformed)
            conn.execute(
                "INSERT INTO events (occurred_at, actor, kind, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (ev["occurred_at"], ev.get("actor"), ev["kind"], payload_json),
            )
        conn.commit()
        # Fold the WAL into the main .db so a later sha256 snapshot is deterministic.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def insert_event(db_path: Path, occurred_at: str, gate: str, note: str) -> None:
    """Append one awaiting_user notify_sent row (for re-export tests)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_CREATE)
        conn.execute(
            "INSERT INTO events (occurred_at, kind, payload_json) "
            "VALUES (?, 'notify_sent', ?)",
            (occurred_at, json.dumps(
                {"kind": "awaiting_user", "task_id": "t", "gate": gate, "note": note}
            )),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def awaiting(gate: str, note: str, task_id: str = "task-x") -> dict:
    """Build an awaiting_user payload dict."""
    return {"kind": "awaiting_user", "task_id": task_id, "gate": gate, "note": note}
