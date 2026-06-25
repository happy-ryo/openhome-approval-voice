#!/usr/bin/env python3
"""CLI tests for pc_exporter.__main__ (Refs #7)."""
from __future__ import annotations

import json
from pathlib import Path

from exporter_helpers import awaiting, seed_state_db

from pc_exporter.__main__ import main


def test_export_cli_writes_queue(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #1")},
    ])
    out = tmp_path / "announce_queue.json"
    rc = main(["export", "--db-path", str(db), "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [i["gate"] for i in data] == ["worker_complete"]


def test_export_cli_limit_flag(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "old")},
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_to_user", "new")},
    ])
    out = tmp_path / "announce_queue.json"
    rc = main(["export", "--db-path", str(db), "--out", str(out), "--limit", "1"])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [i["question"] for i in data] == ["new"]  # most-recent kept


def test_serve_fails_fast_on_bad_db(tmp_path, capsys):
    """serve must NOT bind/serve when the initial export fails (bad --db-path):
    a startup misconfiguration should fail fast, not serve stale/empty data.
    The nonexistent DB makes the up-front export raise BEFORE serve_forever, so
    main returns a non-zero code without hanging."""
    missing = tmp_path / "does_not_exist.db"
    out = tmp_path / "announce_queue.json"
    rc = main(["serve", "--db-path", str(missing), "--out", str(out)])
    assert rc == 2
    assert not out.exists()  # nothing written; no stale/empty queue served
    err = capsys.readouterr().err
    assert "initial export failed" in err
