#!/usr/bin/env python3
"""Build-time loopback smoke: REAL exporter -> HTTP -> REAL ability path (Refs #7).

This is the upgrade of the PR2 stub loopback: instead of a hand-rolled stub, it
wires the *actual* PC-side exporter to the *actual* on-device ability pure-logic
across the real HTTP envelope, proving the two independently-built halves agree
on the section 1.3 contract end to end:

  seed state.db (5 org gates, incl. ci_unconfirmed_head_gate)
    -> core.export_queue            (read-only DB -> §1.3 JSON file)
    -> server (GET /announce_queue.json)        [exporter half / this PR]
    -> json.loads(body)
    -> approval_voice.bridge.items_from_raw     [ability half / PR2]
    -> approval_voice.poller.ReadCursor.unread  (dedup / read-cursor)
    -> approval_voice.renderer.render_speech    (verbatim speech text)

The ability's `do_GET`-equivalent fetch is exercised over a live loopback socket;
the dedup/render/speak steps mirror background.py's watch loop (speak is
simulated by collecting the rendered strings). A second poll proves the
read-cursor suppresses an already-spoken batch (no double readout).
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

from exporter_helpers import awaiting, seed_state_db

from approval_voice.bridge import items_from_raw
from approval_voice.poller import ReadCursor
from approval_voice.renderer import ONE_WAY_SUFFIX, render_speech
from approval_voice.schema import GATES

from pc_exporter import core, server


def _http_get_queue(port: int) -> list:
    """Mirror the ability's outbound GET of the announce queue over HTTP."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", server.QUEUE_ROUTE)
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "application/json"
        return json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


def test_loopback_exporter_to_ability_render(tmp_path):
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #100")},
        {"occurred_at": "2026-06-26T02:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("ci_green_merge_gate", "PR #101")},
        # folds onto escalation (must NOT become ci_merge -> no false CI-green claim)
        {"occurred_at": "2026-06-26T03:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("ci_unconfirmed_head_gate", "PR #102 merged at unconfirmed head")},
        {"occurred_at": "2026-06-26T04:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_to_user", "Issue #7 blocker")},
        {"occurred_at": "2026-06-26T05:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("escalation_reply_forward", "relay to worker")},
    ])
    queue = tmp_path / "announce_queue.json"

    # (1) exporter half: read-only DB -> §1.3 JSON file
    n = core.export_queue(db, queue)
    assert n == 5  # all 5 org gates survive the 5->4 fold (none dropped)

    # (2) serve over the real HTTP envelope
    srv = server.make_server(queue, host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        raw = _http_get_queue(port)

        # (3) ability half: decode -> validate against the §1.3 schema
        items = items_from_raw(raw)  # raises if any item violates the contract
        assert len(items) == 5
        # 5 org gates folded onto the 4 §1.3 gates; escalation appears twice
        assert {i.gate for i in items} == set(GATES)
        assert [i.gate for i in items] == [
            "worker_complete", "ci_merge", "escalation", "escalation", "reply_relay"
        ]

        # (4) dedup + verbatim render (background.py watch loop), speak simulated
        spoken: list[str] = []
        cursor = ReadCursor()
        fresh = cursor.unread(items)
        assert len(fresh) == 5
        for item in fresh:
            text = render_speech(item)
            assert text.endswith(ONE_WAY_SUFFIX)  # one-way reminder always spoken
            spoken.append(text)
        cursor.mark_read(fresh)

        # the unconfirmed-head merge is read as an escalation reading the note
        # verbatim, NOT as a (false) "CI went green" merge announcement.
        unconfirmed = spoken[2]
        assert "PR #102 merged at unconfirmed head" in unconfirmed
        assert "グリーン" not in unconfirmed

        # (5) second poll on the unchanged queue -> read-cursor suppresses all
        raw2 = _http_get_queue(port)
        again = cursor.unread(items_from_raw(raw2))
        assert again == []  # no double readout
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_loopback_new_gate_after_reexport_is_spoken_once(tmp_path):
    """A gate appended to the DB after the first readout is picked up by the
    next GET (server reads the file fresh) and spoken exactly once."""
    db = tmp_path / "state.db"
    seed_state_db(db, [
        {"occurred_at": "2026-06-26T01:00:00.000Z", "kind": "notify_sent",
         "payload": awaiting("worker_completed", "PR #100")},
    ])
    queue = tmp_path / "announce_queue.json"
    core.export_queue(db, queue)

    srv = server.make_server(queue, host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        cursor = ReadCursor()
        first = cursor.unread(items_from_raw(_http_get_queue(port)))
        assert [i.gate for i in first] == ["worker_complete"]
        cursor.mark_read(first)

        from exporter_helpers import insert_event
        insert_event(db, "2026-06-26T06:00:00.000Z", "escalation_to_user", "Issue #9")
        core.export_queue(db, queue)  # re-export to the same served file

        second = cursor.unread(items_from_raw(_http_get_queue(port)))
        assert [i.gate for i in second] == ["escalation"]  # only the new one
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)
