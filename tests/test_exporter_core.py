#!/usr/bin/env python3
"""Unit tests for pc_exporter.core (Refs #7).

Mirrors the salvaged base, adapted to this repo: the seed builds the events
table directly (no claude-org tools.state_db), and the path-resolution tests
exercise this repo's local defaults / fail-fast discovery instead of the
claude-org main-checkout discovery.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from exporter_helpers import awaiting, seed_state_db

from pc_exporter import core


# --------------------------------------------------------------------------
# build_queue mapping
# --------------------------------------------------------------------------
def test_build_queue_maps_all_four_gates(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #100")},
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("ci_green_merge_gate", "PR #101 CI green")},
        {"occurred_at": "2026-06-26T03:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_to_user", "Issue #7 blocker")},
        {"occurred_at": "2026-06-26T04:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_reply_forward", "relay to worker")},
    ])
    conn = core.open_readonly(db)
    try:
        items = core.build_queue(conn)
    finally:
        conn.close()

    assert [i["gate"] for i in items] == [
        "worker_complete", "ci_merge", "escalation", "reply_relay"
    ]
    # field set is exactly the section 1.3 contract for every item
    for it in items:
        assert set(it.keys()) == set(core.FIELDS)
    first = items[0]
    assert first["id"] == "evt-1"
    assert first["title"] == core.TITLE_BY_GATE["worker_complete"]
    assert first["question"] == "PR #100"
    assert first["subject"] == "PR #100"
    assert first["options"] == []
    assert first["created_at"] == "2026-06-26T01:00:00.000Z"


def test_double_predicate_filter(tmp_path):
    """Only rows with events.kind='notify_sent' AND payload kind=awaiting_user
    are emitted. The two decoy rows must NOT appear."""
    db = tmp_path / "state.db"
    seed_state_db(db, [
        # decoy 1: payload kind is not awaiting_user
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": {"kind": "ci_completed", "task_id": "t", "gate": "worker_completed", "note": "x"}},
        # decoy 2: events.kind is not notify_sent (even though payload looks right)
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "awaiting_user",
         "payload": awaiting("worker_completed", "should not appear")},
        # the one real awaiting_user signal
        {"occurred_at": "2026-06-26T03:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "real one")},
    ])
    conn = core.open_readonly(db)
    try:
        items = core.build_queue(conn)
    finally:
        conn.close()
    assert len(items) == 1
    assert items[0]["question"] == "real one"


def test_unknown_gate_skipped_not_errored(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("some_future_gate", "unknown")},
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "known")},
    ])
    conn = core.open_readonly(db)
    try:
        items = core.build_queue(conn)
    finally:
        conn.close()
    assert len(items) == 1
    assert items[0]["gate"] == "worker_complete"


def test_ci_unconfirmed_head_gate_folds_to_escalation(tmp_path):
    """The post-merge fail-closed gate folds onto 'escalation', NOT 'ci_merge':
    the ci_merge renderer asserts CI is green, which would be a false statement
    for an unconfirmed-CI-head merge. escalation reads the note verbatim,
    conveying the real situation accurately. Pin the mapping so the decision is
    explicit and no org awaiting_user gate is dropped from voice."""
    assert core.GATE_MAP["ci_unconfirmed_head_gate"] == "escalation"
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("ci_unconfirmed_head_gate", "PR #636 merged at unconfirmed head")},
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("ci_green_merge_gate", "PR #637")},
    ])
    conn = core.open_readonly(db)
    try:
        items = core.build_queue(conn)
    finally:
        conn.close()
    assert len(items) == 2
    assert items[0]["gate"] == "escalation"
    assert items[0]["question"] == "PR #636 merged at unconfirmed head"
    assert items[0]["subject"] == "PR #636 merged at unconfirmed head"
    assert items[1]["gate"] == "ci_merge"


def test_missing_note_defaults_empty(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": {"kind": "awaiting_user", "task_id": "t", "gate": "worker_completed"}},
    ])
    conn = core.open_readonly(db)
    try:
        items = core.build_queue(conn)
    finally:
        conn.close()
    assert items[0]["question"] == ""
    assert items[0]["subject"] == ""


def test_malformed_payload_tolerated(tmp_path):
    """A non-JSON payload_json decodes to {} and is skipped, not raised."""
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": "{not valid json"},
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "ok")},
    ])
    conn = core.open_readonly(db)
    try:
        items = core.build_queue(conn)
    finally:
        conn.close()
    assert len(items) == 1
    assert items[0]["question"] == "ok"


# --------------------------------------------------------------------------
# Opt-in recency bounds (--since / --limit): default unbounded = spec behavior
# --------------------------------------------------------------------------
def _seed_three(db):
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "old")},
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_to_user", "mid")},
        {"occurred_at": "2026-06-26T03:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_reply_forward", "new")},
    ])


def test_build_queue_default_is_unbounded(tmp_path):
    db = tmp_path / "state.db"
    _seed_three(db)
    conn = core.open_readonly(db)
    try:
        items = core.build_queue(conn)  # no since/limit
    finally:
        conn.close()
    assert [i["question"] for i in items] == ["old", "mid", "new"]


def test_build_queue_since_filters_inclusive(tmp_path):
    db = tmp_path / "state.db"
    _seed_three(db)
    conn = core.open_readonly(db)
    try:
        items = core.build_queue(conn, since="2026-06-26T02:00:00.000Z")
    finally:
        conn.close()
    # inclusive lower bound: the mid + new rows, not the old one
    assert [i["question"] for i in items] == ["mid", "new"]


def test_build_queue_limit_keeps_most_recent_chronological(tmp_path):
    db = tmp_path / "state.db"
    _seed_three(db)
    conn = core.open_readonly(db)
    try:
        items = core.build_queue(conn, limit=2)
    finally:
        conn.close()
    # most-recent 2, still in chronological order
    assert [i["question"] for i in items] == ["mid", "new"]


def test_build_queue_limit_zero_is_unlimited(tmp_path):
    db = tmp_path / "state.db"
    _seed_three(db)
    conn = core.open_readonly(db)
    try:
        items = core.build_queue(conn, limit=0)
    finally:
        conn.close()
    assert len(items) == 3


# --------------------------------------------------------------------------
# Contract drift guard
# --------------------------------------------------------------------------
def test_contract_field_set_constant():
    assert set(core.FIELDS) == {
        "id", "gate", "title", "question", "subject", "options", "created_at"
    }


def test_contract_gate_values_constant():
    assert set(core.GATE_MAP.values()) == {
        "worker_complete", "ci_merge", "escalation", "reply_relay"
    }
    assert set(core.TITLE_BY_GATE.keys()) == set(core.GATE_MAP.values())


def test_contract_matches_ability_schema():
    """The exporter FIELDS / gate enum must match the on-device ability schema
    verbatim (the cross-project section 1.3 contract). If approval_voice/schema.py
    changes, this test fails so the two halves never silently diverge."""
    from approval_voice.schema import GATES, ITEM_FIELDS

    assert tuple(core.FIELDS) == tuple(ITEM_FIELDS)
    assert set(core.GATE_MAP.values()) == set(GATES)


# --------------------------------------------------------------------------
# Public hygiene: internal ids never cross the boundary (choke point)
# --------------------------------------------------------------------------
def test_public_hygiene_no_internal_ids(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "actor": "secretary-pane",
         "payload": awaiting("worker_completed", "PR #100", task_id="SECRET-TASK-99")},
    ])
    out = tmp_path / "announce_queue.json"
    core.export_queue(db, out)
    serialized = out.read_text(encoding="utf-8")
    assert "SECRET-TASK-99" not in serialized
    assert "task_id" not in serialized
    assert "actor" not in serialized
    assert "secretary-pane" not in serialized
    for it in json.loads(serialized):
        assert set(it.keys()) == set(core.FIELDS)


# --------------------------------------------------------------------------
# Atomic write + export_queue
# --------------------------------------------------------------------------
def test_export_queue_atomic_writes_valid_array(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("ci_green_merge_gate", "PR #101")},
    ])
    out = tmp_path / "sub" / "announce_queue.json"  # parent must be created
    n = core.export_queue(db, out)
    assert n == 1
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert data[0]["gate"] == "ci_merge"
    # no leftover temp files in the directory
    leftovers = list(out.parent.glob(".announce_queue.*.tmp"))
    assert leftovers == []


def test_atomic_write_japanese_roundtrip(tmp_path):
    """Japanese title constants survive into the utf-8 file (ensure_ascii=False)."""
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #1")},
    ])
    out = tmp_path / "announce_queue.json"
    core.export_queue(db, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0]["title"] == "ワーカー完了の承認待ち"


# --------------------------------------------------------------------------
# Read-only proof: db file bytes unchanged across export (WAL target)
# --------------------------------------------------------------------------
def test_export_does_not_mutate_db(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #100")},
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_to_user", "Issue #7")},
    ])
    # seed_state_db checkpointed the WAL into the main .db file already, so the
    # snapshot below is meaningful: the exporter must not touch these bytes.
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    core.export_queue(db, tmp_path / "out.json")
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after


def test_open_readonly_rejects_writes(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #100")},
    ])
    conn = core.open_readonly(db)
    try:
        # query_only=ON is an intentional belt-and-suspenders guard on top of the
        # mode=ro URI; assert it is actually applied so a regression dropping it
        # is caught (mode=ro alone would still make the INSERT below raise).
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO events (occurred_at, kind) VALUES ('x', 'y')")
            conn.commit()
    finally:
        conn.close()


def test_export_reads_live_wal_without_mutating(tmp_path):
    """The live target is a WAL-mode state.db held open by the org write process,
    with committed-but-uncheckpointed rows in a NON-empty -wal sidecar.

    The discriminating assertion is ``n == 1``: a ``mode=ro`` open must actually
    SEE the committed WAL rows while the writer holds the db open (this is the
    real production read path -- the exporter reads live state, not a stale
    snapshot). It must also not fold/mutate the WAL or the main db. The -shm is
    deliberately NOT hashed: readers legitimately update its read-marks."""
    db = tmp_path / "state.db"
    # A persistent writer (simulating the live org process) keeps the WAL + -shm
    # alive THROUGH the read; a mode=ro connection cannot read a WAL db without it.
    writer = sqlite3.connect(str(db))
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,"
            " actor TEXT, kind TEXT NOT NULL, payload_json TEXT)"
        )
        writer.execute(
            "INSERT INTO events (occurred_at, kind, payload_json) "
            "VALUES (?, 'notify_sent', ?)",
            ("2026-06-26T01:00:00.000Z",
             json.dumps(awaiting("worker_completed", "PR #100"))),
        )
        writer.commit()  # committed but NOT checkpointed -> lives in -wal
        wal = Path(str(db) + "-wal")
        assert wal.exists() and wal.stat().st_size > 0  # precondition: live-like

        db_before = hashlib.sha256(db.read_bytes()).hexdigest()
        wal_before = hashlib.sha256(wal.read_bytes()).hexdigest()
        n = core.export_queue(db, tmp_path / "out.json")
        assert n == 1  # mode=ro SEES the committed-but-uncheckpointed WAL row
        assert hashlib.sha256(db.read_bytes()).hexdigest() == db_before
        assert hashlib.sha256(wal.read_bytes()).hexdigest() == wal_before
    finally:
        writer.close()


# --------------------------------------------------------------------------
# Path resolution (this repo's local defaults / fail-fast discovery)
# --------------------------------------------------------------------------
def test_resolve_db_path_precedence(tmp_path, monkeypatch):
    # --db-path wins
    assert core.resolve_db_path(str(tmp_path / "a.db")) == (tmp_path / "a.db")
    # env next
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "b.db"))
    assert core.resolve_db_path(None) == (tmp_path / "b.db")


def test_resolve_db_path_fails_fast_without_org(monkeypatch):
    """With no --db-path, no $STATE_DB_PATH, and no importable claude-org
    discovery, resolve_db_path must raise an actionable error rather than
    silently reading/creating an empty DB. (openhome owns no state.db.)"""
    monkeypatch.delenv("STATE_DB_PATH", raising=False)
    monkeypatch.setitem(__import__("sys").modules, "tools.state_db.discover", None)
    with pytest.raises(RuntimeError) as exc:
        core.resolve_db_path(None)
    assert "--db-path" in str(exc.value)


def test_default_out_path_is_local_and_writable():
    """The default queue file lives under THIS repo's .state/ (writable,
    gitignored), never next to the reference-only claude-org state.db."""
    out = core.default_out_path()
    assert out.name == "announce_queue.json"
    assert out.parent.name == ".state"
    assert out.parent.parent == core.repo_root()
