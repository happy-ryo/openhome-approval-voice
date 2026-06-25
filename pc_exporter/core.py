#!/usr/bin/env python3
"""Transport-independent core of the PC-side approval exporter (Refs #7).

This module is the (a) half of the exporter described in the design SoT
(``docs/design.md`` M3.3.1 / M3.1-s.5): it reads the claude-org state DB
``events`` table READ-ONLY, maps ``awaiting_user`` signals into the section 1.3
``AnnounceItem`` shape (``approval_voice/schema.py`` ``ITEM_FIELDS``), and writes
the resulting JSON array atomically to a file on disk (temp -> os.replace).

PC side, NOT bundled into the DevKit ability. This package legitimately uses
``sqlite3`` / raw file I/O / module-scope imports; the OpenHome add-capability
sandbox rules (which apply to ``openhome_ability/`` + ``approval_voice/``) do
NOT apply here, and the bundle scanners are scoped to exclude this package.

It deliberately carries NO HTTP / transport code so a future scp/push fallback
can reuse ``export_queue()`` unchanged. The HTTP layer (``server.py``) reads the
file this module wrote; it never touches the DB.

Read-only invariant
-------------------
We open with a ``file:...?mode=ro`` URI (fails fast if the DB is missing rather
than creating an empty one) plus ``PRAGMA query_only=ON``. We never issue
``PRAGMA journal_mode=WAL`` (a write to the db header) or run migrations, so the
db file bytes are never mutated. The live target is a WAL-mode state.db with
active ``-wal`` / ``-shm`` sidecars; ``mode=ro`` reads it without writing.

Public hygiene
--------------
The org-gate -> section 1.3-gate mapping lives ONLY here (this repo is PUBLIC,
but the gate-name mapping exposure is user-approved). Only the human-facing
``note`` (thin version) and an opaque ``evt-<id>`` cross into an AnnounceItem.
Internal ids (task_id), paths, actor and raw org schema names never cross.

All CLI / print strings elsewhere stay ASCII (cp932 console safety); the
Japanese title constants below are only ever written into the utf-8 JSON file
with ``ensure_ascii=False`` -- never printed to a console.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional

# --- org gate -> section 1.3 gate mapping (public-hygiene choke point) -----
# Rows whose payload gate is not a key here are skipped defensively (not an
# error). The section 1.3 gate enum is FIXED at exactly 4 target values
# (worker_complete / ci_merge / escalation / reply_relay) and is shared verbatim
# with the PR2 ability (approval_voice/schema.py GATES), so the mapping cannot
# introduce a 5th *target* value; multiple org gates may fold onto the same
# section 1.3 gate.
#
# Production emits 5 distinct awaiting_user org gates. The 5th --
# 'ci_unconfirmed_head_gate' (org-pull-request fail-closed post-merge
# confirmation) -- is folded onto 'escalation', NOT 'ci_merge': the section 1.3
# ci_merge renderer asserts "CI がグリーンになりました", which would be a FALSE
# statement for an unconfirmed-CI-head merge. The escalation renderer reads the
# note (question) verbatim, so the actual situation ("PR #N merged at unconfirmed
# head ...") is conveyed accurately. No org awaiting_user gate is dropped from
# the DevKit voice surface.
GATE_MAP: dict[str, str] = {
    "worker_completed": "worker_complete",
    "ci_green_merge_gate": "ci_merge",
    "ci_unconfirmed_head_gate": "escalation",
    "escalation_to_user": "escalation",
    "escalation_reply_forward": "reply_relay",
}

# --- fixed per-(section 1.3)-gate Japanese headline constants --------------
# Written to the utf-8 JSON file only (ensure_ascii=False); never printed.
TITLE_BY_GATE: dict[str, str] = {
    "worker_complete": "ワーカー完了の承認待ち",
    "ci_merge": "マージ承認待ち",
    "escalation": "エスカレーション",
    "reply_relay": "返答転送待ち",
}

# --- the exact section 1.3 AnnounceItem field set (contract; frozen) -------
# Mirrors approval_voice/schema.py ITEM_FIELDS verbatim (cross-project contract).
FIELDS: tuple[str, ...] = (
    "id",
    "gate",
    "title",
    "question",
    "subject",
    "options",
    "created_at",
)


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open ``db_path`` strictly read-only.

    Uses the ``file:...?mode=ro`` URI (fails fast if the DB is missing rather
    than creating an empty one) plus ``PRAGMA query_only=ON`` as a
    belt-and-suspenders guard. Never applies WAL or runs migrations, so the
    db file bytes are never mutated.
    """
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def repo_root() -> Path:
    """The openhome-approval-voice repo root (this file is <root>/pc_exporter/core.py)."""
    return Path(__file__).resolve().parent.parent


def resolve_db_path(cli_override: Optional[str]) -> Path:
    """Resolve the state DB path.

    Precedence: ``--db-path`` > ``$STATE_DB_PATH`` > org-context discovery.

    This repo (openhome-approval-voice) does NOT own a state.db; the exporter
    reads claude-org's live ``.state/state.db``. So the normal path is to pass
    ``--db-path`` (or set ``$STATE_DB_PATH``). The discovery fallback only works
    when the process can import ``tools.state_db.discover`` (i.e. it is being run
    from inside a claude-org checkout that puts ``tools`` on ``sys.path``); when
    that module is unavailable we fail fast with an actionable message rather
    than silently creating / reading an empty DB.
    """
    if cli_override:
        return Path(cli_override).expanduser()
    env = os.environ.get("STATE_DB_PATH")
    if env and env.strip():
        return Path(env).expanduser()
    try:
        from tools.state_db.discover import resolve_state_db_path  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "no state.db path given and claude-org discovery is unavailable. "
            "Pass --db-path /path/to/.state/state.db or set STATE_DB_PATH."
        ) from exc
    return resolve_state_db_path(None)


def default_out_path() -> Path:
    """Default queue output file: ``<repo_root>/.state/announce_queue.json``.

    Anchored to THIS (openhome) repo, a writable location -- never next to the
    source claude-org state.db (that repo is reference-only). ``.state/`` is
    gitignored so a real export is not committed (public-hygiene).
    """
    return repo_root() / ".state" / "announce_queue.json"


def _load_payload(raw: Any) -> dict[str, Any]:
    """Decode ``events.payload_json`` defensively into a dict.

    Anything that is not a JSON object decodes to ``{}`` so callers can
    ``.get()`` without guarding. Mirrors the house tolerant reader.
    """
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _row_to_item(event_id: int, occurred_at: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Map one awaiting_user event row to a section 1.3 AnnounceItem dict.

    Returns ``None`` when the payload gate is not in ``GATE_MAP`` (defensive
    skip, not an error). Only the opaque evt-id and the human-facing ``note``
    cross the public boundary; internal ids never do.
    """
    org_gate = payload.get("gate")
    sec_gate = GATE_MAP.get(org_gate)
    if sec_gate is None:
        return None
    note = payload.get("note")
    note = note if isinstance(note, str) else ""
    return {
        "id": f"evt-{event_id}",
        "gate": sec_gate,
        "title": TITLE_BY_GATE[sec_gate],
        "question": note,
        # subject is read by all four consumer renderers, so it must be
        # non-empty human context; the note (PR#/Issue#/summary) is the right
        # public-facing value. task_id (internal) must NOT be used here.
        "subject": note,
        "options": [],
        "created_at": occurred_at,
    }


def build_queue(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[dict[str, Any]]:
    """Read awaiting_user events from ``conn`` and return a section 1.3 array.

    Both predicates are required (see Refs #7): the row's ``events.kind`` is
    ``'notify_sent'`` AND the payload's own ``kind`` is ``'awaiting_user'``.
    Filtering on ``events.kind='awaiting_user'`` would return zero rows. The
    payload-kind / gate filtering is done in Python (single tolerant decode)
    to avoid json_extract NULL subtleties.

    ``since`` (ISO8601 string, inclusive) and ``limit`` (most-recent N) are
    OPT-IN operator bounds, both unset by default so the spec behavior -- map
    every notify_sent awaiting_user event -- is preserved. They exist so an
    operator can avoid replaying the entire historical event log to a fresh
    consumer cursor on first install / cursor reset; ``occurred_at`` is an
    ISO8601 timestamp so a lexical ``>=`` is also chronological. NOTE: this is a
    coarse recency bound, NOT a pending-state filter -- the thin readout reads
    notify_sent events (which include already-resolved approvals); the accurate
    "currently pending" source (pending_decisions) is a deferred phase. A
    ``limit`` is therefore a count cap only and can drop genuinely-unread items
    in a >limit backlog burst; prefer leaving it unset unless first-run replay
    is a concrete problem.
    """
    if since is not None:
        rows = conn.execute(
            "SELECT id, occurred_at, payload_json FROM events "
            "WHERE kind = 'notify_sent' AND occurred_at >= ? "
            "ORDER BY occurred_at, id",
            (since,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, occurred_at, payload_json FROM events "
            "WHERE kind = 'notify_sent' "
            "ORDER BY occurred_at, id"
        ).fetchall()
    items: List[dict[str, Any]] = []
    for row in rows:
        payload = _load_payload(row["payload_json"])
        if payload.get("kind") != "awaiting_user":
            continue
        item = _row_to_item(int(row["id"]), row["occurred_at"], payload)
        if item is not None:
            items.append(item)
    # limit keeps the most-recent N (chronological order preserved). <=0 / None
    # means unlimited.
    if limit is not None and limit > 0 and len(items) > limit:
        items = items[-limit:]
    return items


def _replace_with_retry(src: str, dst: Path, attempts: int = 5) -> None:
    """``os.replace(src, dst)`` with a short bounded retry on PermissionError.

    On Windows ``os.replace`` must delete the destination; if an HTTP GET holds
    a read handle on ``dst`` at that instant (CPython read open() lacks
    FILE_SHARE_DELETE) the replace raises ERROR_SHARING_VIOLATION ->
    PermissionError. The collision window is sub-millisecond, so a few short
    retries make the swap succeed without surfacing transient noise. The replace
    stays atomic (all-or-nothing); a reader always sees the old or new file, never
    a partial one. POSIX never hits this path (replace over an open file is fine).
    """
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.02)


def atomic_write_json(payload: Any, out_path: Path) -> None:
    """Write ``payload`` as JSON to ``out_path`` atomically (temp -> os.replace).

    The temp file is created in the SAME directory as ``out_path`` so
    ``os.replace`` is a same-volume rename (a cross-volume rename raises on
    Windows). On any error the temp file is removed.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(out_path.parent), prefix=".announce_queue.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp_name, out_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def export_queue(
    db_path: Path,
    out_path: Path,
    since: Optional[str] = None,
    limit: Optional[int] = None,
) -> int:
    """End-to-end (a) step: read DB read-only -> build section 1.3 array ->
    atomic write to ``out_path``. Returns the number of items written.

    ``since`` / ``limit`` are the opt-in recency bounds documented on
    :func:`build_queue` (default unset = full history, preserving spec behavior).
    """
    conn = open_readonly(Path(db_path))
    try:
        items = build_queue(conn, since=since, limit=limit)
    finally:
        conn.close()
    atomic_write_json(items, Path(out_path))
    return len(items)
